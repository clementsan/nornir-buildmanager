import copy
import logging
import math
import os.path

import numpy

import nornir_buildmanager
from nornir_buildmanager.validation import transforms
from nornir_imageregistration.files import mosaicfile
from nornir_imageregistration.image_stats import Prune
from nornir_shared.files import RemoveOutdatedFile
from nornir_shared.histogram import Histogram
import nornir_shared.misc
import nornir_shared.plot as plot
import nornir_shared.prettyoutput as prettyoutput


class PruneObj:
    """Executes ir-prune and produces a histogram"""

    ImageMapFileTemplate = "PruneScores%s.txt"

    HistogramXMLFileTemplate = 'PruneScores%s.xml'
    HistogramPNGFileTemplate = 'PruneScores%s.png'
    HistogramSVGFileTemplate = 'PruneScores%s.svg'

    ElementVersion = 1

    def __init__(self, MapToImageScore=None, Tolerance=None):
        self.Tolerance = Tolerance
        if self.Tolerance is None:
            self.Tolerance = 5

        if MapToImageScore is None:
            self.MapImageToScore = dict()
        else:
            self.MapImageToScore = MapToImageScore

    @classmethod
    def _GetThreshold(cls, PruneNode: nornir_buildmanager.volumemanager.PruneNode, ThresholdParameter) -> float:
        '''Return the threshold value that should be used.
           If a UserRequestedCutoff is specified use that.
           If a Threshold is passed only use if no UserRequstedValue exists
           '''

        Threshold = None
        if PruneNode.UserRequestedCutoff is not None:
            Threshold = PruneNode.UserRequestedCutoff
        else:
            Threshold = ThresholdParameter

        if Threshold is None:
            Threshold = 0.0

        return Threshold

    @classmethod
    def _TryUpdateUndefinedThresholdFromParameter(cls, PruneNode, ThresholdParameter):
        '''If a Threshold parameter is passed set the UserRequested cutoff if it is not already specified'''

        if PruneNode.UserRequestedCutoff is None and ThresholdParameter is not None:
            PruneNode.UserRequestedCutoff = ThresholdParameter
            return True

        return False

    @classmethod
    def PruneMosaic(cls, Parameters, PruneNode, TransformNode, OutputTransformName=None, Logger=None, **kwargs):
        '''@ChannelNode 
           Uses a PruneData node to prune the specified mosaic file'''

        threshold_precision = nornir_buildmanager.volumemanager.TransformNode.get_threshold_precision()  # Number of digits to save in XML file

        if Logger is None:
            Logger = logging.getLogger(__name__ + '.PruneMosaic')

        Threshold = cls._GetThreshold(PruneNode, Parameters.get('Threshold', None))
        if Threshold is not None:
            Threshold = TransformNode.round_precision_value(Threshold)  # round(Threshold, threshold_precision)
            Parameters['Threshold'] = Threshold  # Update the Parameters so the Mangled name is correct

        cls._TryUpdateUndefinedThresholdFromParameter(PruneNode, Threshold)

        if OutputTransformName is None:
            OutputTransformName = 'Prune'

        InputTransformNode = TransformNode
        TransformParent = InputTransformNode.Parent

        OutputMosaicName = OutputTransformName + nornir_shared.misc.GenNameFromDict(Parameters) + '.mosaic'

        MangledName = nornir_shared.misc.GenNameFromDict(Parameters)

        MosaicDir = os.path.dirname(InputTransformNode.FullPath)
        OutputMosaicFullPath = os.path.join(MosaicDir, OutputMosaicName)

        # Check if there is an existing prune map, and if it exists if it is out of date
        PruneNodeParent = PruneNode.Parent

        transforms.RemoveWhere(TransformParent, 'Transform[@Name="' + OutputTransformName + '"]',
                               lambda t: (t.Threshold != Threshold) or (t.Type != MangledName))

        '''TODO: Add function to remove duplicate Prune Transforms with different thresholds'''

        TransformParent.RemoveOldChildrenByAttrib('Transform', 'Name', OutputTransformName)

        PruneDataNode = PruneNode.find('Data')
        if PruneDataNode is None:
            Logger.warning("Did not find expected prune data node")
            return None

        OutputTransformNode = transforms.LoadOrCleanExistingTransformForInputTransform(channel_node=TransformParent,
                                                                                       InputTransformNode=InputTransformNode,
                                                                                       OutputTransformPath=OutputMosaicName)
        if OutputTransformNode is not None:
            if OutputTransformNode.Locked:
                Logger.info("Skipping locked transform %s" % OutputTransformNode.FullPath)
                return None

            OutputTransformNode = transforms.RemoveOnMismatch(OutputTransformNode, 'InputPruneDataChecksum',
                                                              PruneDataNode.Checksum)
            OutputTransformNode = transforms.RemoveOnMismatch(OutputTransformNode, 'Threshold', Threshold,
                                                              Precision=threshold_precision)

        # Add the Prune Transform node if it is missing
        if OutputTransformNode is None:
            OutputTransformNode = nornir_buildmanager.volumemanager.TransformNode.Create(Name=OutputTransformName,
                                                                                         Type=MangledName,
                                                                                         InputTransformChecksum=InputTransformNode.Checksum)
            TransformParent.append(OutputTransformNode)
        elif os.path.exists(OutputTransformNode.FullPath):
            # The meta-data and output exist, check if the histogram image exists and then move on
            PruneObjInstance = PruneObj.TryUpdateHistogram(PruneNode, Threshold)
            if PruneObjInstance is not None:
                return PruneNodeParent

            return None

        OutputTransformNode.SetTransform(InputTransformNode)
        OutputTransformNode.InputPruneDataType = PruneNode.Type
        OutputTransformNode.InputPruneDataChecksum = PruneDataNode.Checksum
        if not Threshold is None:
            OutputTransformNode.Threshold = Threshold

        PruneObjInstance = PruneObj.TryUpdateHistogram(PruneNode, Threshold)
        if PruneObjInstance is None:
            PruneObjInstance = cls.ReadPruneMap(PruneDataNode.FullPath)

        assert (not PruneObjInstance is None)

        if OutputTransformNode is None:
            if not hasattr(OutputTransformNode, Threshold):
                OutputTransformNode.Threshold = Threshold

            if OutputTransformNode.Threshold != Threshold:
                if os.path.exists(OutputMosaicFullPath):
                    os.remove(OutputMosaicFullPath)

        if not os.path.exists(OutputMosaicFullPath):
            try:
                PruneObjInstance.WritePruneMosaic(PruneNodeParent.FullPath, InputTransformNode.FullPath,
                                                  OutputMosaicFullPath, Tolerance=Threshold)
            except (KeyError, ValueError):
                os.remove(PruneDataNode.FullPath)
                PruneNode.remove(PruneDataNode)
                prettyoutput.LogErr("Remove prune data for section " + PruneDataNode.FullPath)
                return PruneNodeParent

        OutputTransformNode.Type = MangledName
        OutputTransformNode.Name = OutputTransformName

        # Setting this value automatically converts the double to a string using the %g formatter.  This is a precision of two.  The RemoveOnMismatch value needs to use a matching precision
        OutputTransformNode.Threshold = Threshold
        OutputTransformNode.ResetChecksum()

        # OutputTransformNode.Checksum = mosaicfile.MosaicFile.LoadChecksum(OutputTransformNode.FullPath)
        return [TransformParent, PruneNodeParent]

    @classmethod
    def TryUpdateHistogram(cls, prune_node, Threshold: float):
        '''
        Updates the prune histogram if needed.
        :return: If the histogram is updated a PruneObj is loaded and returned
            otherwise None
        '''
        PruneDataNode = prune_node.DataNode
        if PruneDataNode is None:
            Logger = logging.getLogger(__name__ + '.ReadPruneDataNode')
            Logger.warning("Did not find expected prune data node")
            return None

        HistogramXMLFile = PruneObj.HistogramXMLFileTemplate % prune_node.Type
        HistogramImageFile = PruneObj.HistogramSVGFileTemplate % prune_node.Type
        HistogramXMLFileFullPath = os.path.join(prune_node.Parent.FullPath, HistogramXMLFile)
        HistogramImageFileFullPath = os.path.join(prune_node.Parent.FullPath, HistogramImageFile)

        threshold_precision = nornir_buildmanager.volumemanager.TransformNode.get_threshold_precision()  # Number of digits to save in XML file

        try:
            RemoveOutdatedFile(PruneDataNode.FullPath, HistogramImageFileFullPath)
            RemoveOutdatedFile(PruneDataNode.FullPath, HistogramXMLFileFullPath)

            HistogramImageNode = prune_node.ImageNode
            if not HistogramImageNode is None:
                HistogramImageNode = transforms.RemoveOnMismatch(HistogramImageNode, 'Threshold', Threshold,
                                                                 Precision=threshold_precision)

            if HistogramImageNode is None or not os.path.exists(HistogramImageFileFullPath):
                HistogramImageNode = nornir_buildmanager.volumemanager.ImageNode.Create(HistogramImageFile)
                (added, HistogramImageNode) = prune_node.UpdateOrAddChild(HistogramImageNode)
                if not added:
                    # Handle the case where the path is different, such as when we change the extension type
                    if os.path.exists(HistogramImageNode.FullPath):
                        os.remove(HistogramImageNode.FullPath)
                    HistogramImageNode.Path = HistogramImageFile

                HistogramImageNode.Threshold = Threshold

                PruneObjInstance = PruneObj.ReadPruneMap(PruneDataNode.FullPath)

                PruneObj.CreateHistogram(PruneObjInstance.MapImageToScore, HistogramXMLFileFullPath)
                assert (HistogramImageNode.FullPath == HistogramImageFileFullPath)
                # if Async:
                # pool = nornir_pools.GetMultithreadingPool("Histograms")
                # pool.add_task("Create Histogram %s" % HistogramImageFile, plot.Histogram, HistogramXMLFile, HistogramImageFile, LinePosList=self.Tolerance, Title=Title)
                # else:
                plot.Histogram(HistogramXMLFileFullPath, HistogramImageNode.FullPath, LinePosList=Threshold,
                               Title="Threshold " + str(Threshold))
                prettyoutput.Log('Generated histogram image {0}'.format(HistogramImageNode.FullPath))

                return PruneObjInstance

        except Exception as E:
            prettyoutput.LogErr(f"Exception creating prunemap histogram\n{E}")
            pass

    @classmethod
    def CalculatePruneScores(cls, Parameters, FilterNode, Downsample, TransformNode, OutputFile=None, Logger=None,
                             **kwargs):
        '''@FilterNode
            Calculate the prune scores for a filter and level'''
        # VolumeManager.NodeManager.GetParent(Entry.NodeList)
        # Check for an existing prune map

        Overlap = float(Parameters.get('Overlap', 0.1))

        if OutputFile is None:
            OutputFile = 'PruneScores'

        assert (isinstance(Downsample, int) or isinstance(Downsample, float))

        [created, LevelNode] = FilterNode.TilePyramid.GetOrCreateLevel(Downsample)

        if LevelNode is None:
            prettyoutput.LogErr("Missing InputPyramidLevelNode attribute on PruneTiles")
            Logger.error("Missing InputPyramidLevelNode attribute on PruneTiles")
            return

        if TransformNode is None:
            prettyoutput.LogErr("Missing TransformNode attribute on PruneTiles")
            Logger.error("Missing TransformNode attribute on PruneTiles")
            return

        FilterNode = LevelNode.FindParent("Filter")

        # Record the downsample level the values are calculated at:
        Parameters['Level'] = str(LevelNode.Downsample)
        Parameters['Filter'] = FilterNode.Name

        MangledName = nornir_shared.misc.GenNameFromDict(Parameters) + '_' + TransformNode.Type
        OutputFile = OutputFile + MangledName + '.txt'

        SaveRequired = False
        PruneMapElement = FilterNode.GetChildByAttrib('Prune', 'Overlap', Overlap)
        PruneMapElement = transforms.RemoveOnMismatch(PruneMapElement, 'InputTransformChecksum', TransformNode.Checksum)

        if not PruneMapElement is None:
            if LevelNode.TilesValidated is not None:
                PruneMapElement = transforms.RemoveOnMismatch(PruneMapElement, 'NumImages', LevelNode.TilesValidated)

        if PruneMapElement is None:
            PruneMapElement = nornir_buildmanager.volumemanager.PruneNode.Create(Overlap=Overlap, Type=MangledName)
            [SaveRequired, PruneMapElement] = FilterNode.UpdateOrAddChildByAttrib(PruneMapElement, 'Overlap')
        else:
            # If meta-data and the data file exist, nothing to do
            if os.path.exists(PruneMapElement.DataFullPath):
                return

        # Create file holders for the .xml and .png files
        PruneDataNode = nornir_buildmanager.volumemanager.DataNode.Create(OutputFile)
        [added, PruneDataNode] = PruneMapElement.UpdateOrAddChild(PruneDataNode)

        FullTilePath = LevelNode.FullPath

        TransformObj = mosaicfile.MosaicFile.Load(TransformNode.FullPath)
        TransformObj.RemoveInvalidMosaicImages(FullTilePath)

        files = []
        for f in list(TransformObj.ImageToTransformString.keys()):
            files.append(os.path.join(FullTilePath, f))

        TileToScore = Prune(files, Overlap)

        prune = PruneObj(TileToScore)

        prune.WritePruneMap(PruneDataNode.FullPath)

        PruneMapElement.InputTransformChecksum = TransformNode.Checksum
        PruneMapElement.NumImages = len(TileToScore)

        return FilterNode

    #
    def WritePruneMap(self, MapImageToScoreFile):

        if len(self.MapImageToScore) == 0:
            prettyoutput.LogErr('No prune scores to write to file ' + MapImageToScoreFile)
            return

        with open(MapImageToScoreFile, 'w') as outfile:

            if len(list(self.MapImageToScore.keys())) == 0:
                if os.path.exists(MapImageToScoreFile):
                    os.remove(MapImageToScoreFile)

                prettyoutput.Log("No prune scores present in PruneMap being saved: " + MapImageToScoreFile)
                outfile.close()
                return

            for f in sorted(self.MapImageToScore.keys()):
                score = self.MapImageToScore[f]

                outfile.write(f + '\t' + str(score) + '\n')

            outfile.close()

    @classmethod
    def ReadPruneMap(cls, MapImageToScoreFile):

        assert (os.path.exists(MapImageToScoreFile))
        infile = open(MapImageToScoreFile, 'r')
        lines = infile.readlines()
        #      prettyoutput.Log( lines)
        infile.close()

        assert (len(lines) > 0)
        if len(lines) == 0:
            return None

        MapImageToScore = dict()

        for line in lines:
            [image, score] = line.split('\t')
            score = float(score.strip())

            MapImageToScore[image] = score

        return PruneObj(MapImageToScore)

    @staticmethod
    def CreateHistogram(MapImageToScore, HistogramXMLFile, MapImageToScoreFile=None):
        if len(list(MapImageToScore.items())) == 0 and MapImageToScoreFile is not None:
            #         prettyoutput.Log( "Reading scores, MapImageToScore Empty " + MapImageToScoreFile)
            PruneObj.ReadPruneMap(MapImageToScoreFile)
        #         prettyoutput.Log( "Read scores complete: " + str(self.MapImageToScore))

        if len(list(MapImageToScore.items())) == 0:
            prettyoutput.Log("No prune scores to create histogram with")
            return

        scores = [None] * len(list(MapImageToScore.items()))
        numScores = len(scores)

        i = 0
        for pair in list(MapImageToScore.items()):
            #         prettyoutput.Log("pair: " + str(pair))
            scores[i] = pair[1]
            i += 1

        # Figure out what type of histogram we should create
        #       prettyoutput.Log('Scores: ' + str(scores))
        minVal = min(scores)
        # prettyoutput.Log("MinVal: " + str(minVal))
        maxVal = max(scores)
        # prettyoutput.Log("MaxVal: " + str(maxVal))
        mean = sum(scores) / len(scores)

        # prettyoutput.Log("Mean: " + str(mean))
        #
        #         StdDevScalar = 1.0
        #         if numScores > 1:
        #             StdDevScalar = 1.0 / float(numScores - 1.0)
        #
        #         total = 0
        #         # Calc the std deviation
        #         for score in scores:
        #             temp = score - mean
        #             temp = temp * temp
        #             total = total + (temp * StdDevScalar)

        StdDev = numpy.std(scores)
        # prettyoutput.Log("StdDev: " + str(StdDev))

        numBins = 1
        if numScores > 1:
            numBins = int(math.ceil((maxVal - minVal) / (StdDev / 10.0)))

        # prettyoutput.Log("NumBins: " + str(numBins))

        if numBins < 10:
            numBins = 10

        if numBins > len(scores):
            numBins = len(scores)

        # prettyoutput.Log("Final NumBins: " + str(numBins))

        H = Histogram.Init(minVal, maxVal, numBins)
        H.Add(scores)
        H.Save(HistogramXMLFile)

        print("Created Histogram %s" % HistogramXMLFile)

    def WritePruneMosaic(self, path, SourceMosaic, TargetMosaic='prune.mosaic', Tolerance=5):
        '''
        Remove tiles from the source mosaic with scores less than Tolerance and
        write the new mosaic to TargetMosaic.
        Raises a key error if image in prude scores does not exist in .mosaic file
        Raises a value error if the threshold removes all tiles in the mosaic.
        :return: Number of tiles removed
        '''

        if not isinstance(Tolerance, float):
            Tolerance = float(Tolerance)

        SourceMosaicFullPath = os.path.join(path, SourceMosaic)
        TargetMosaicFullPath = os.path.join(path, TargetMosaic)

        mosaic = mosaicfile.MosaicFile.Load(SourceMosaicFullPath)

        # We copy this because if an input image is missing there will not be a prune score and it should be removed from the .mosaic file
        inputImageToTransforms = copy.deepcopy(mosaic.ImageToTransformString)
        mosaic.ImageToTransformString.clear()

        numRemoved = 0

        for item in list(self.MapImageToScore.items()):
            filename = item[0]
            score = item[1]

            if score >= Tolerance:
                keyVal = filename
                if not keyVal in inputImageToTransforms:
                    keyVal = os.path.basename(filename)
                    if not keyVal in inputImageToTransforms:
                        raise KeyError("PruneObj: Cannot locate image file in .mosaic " + keyVal)

                mosaic.ImageToTransformString[keyVal] = inputImageToTransforms[keyVal]
            else:
                numRemoved += 1

        if len(mosaic.ImageToTransformString) <= 0:
            errMsg = "All tiles removed when using threshold = " + str(Tolerance) + "\nThe prune request was ignored"
            prettyoutput.LogErr(errMsg)
            raise ValueError(errMsg)
        else:
            prettyoutput.Log("Removed " + str(numRemoved) + " tiles pruning mosaic " + TargetMosaic)

        mosaic.Save(TargetMosaicFullPath)

        return numRemoved
