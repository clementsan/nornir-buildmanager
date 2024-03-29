'''
Created on Jun 22, 2012

@author: Jamesan
'''
import math
import random
import shutil
import subprocess
import tempfile
import typing

from nornir_buildmanager.exceptions import NornirUserException
from nornir_buildmanager.metadatautils import *
import nornir_buildmanager.operations.helpers.mosaicvolume as mosaicvolume
import nornir_buildmanager.operations.helpers.stosgroupvolume as stosgroupvolume
from nornir_buildmanager.validation import transforms
from nornir_buildmanager.volumemanager import *
import nornir_imageregistration
from nornir_imageregistration import assemble, local_distortion_correction, mosaic
from nornir_imageregistration.files import stosfile
import nornir_imageregistration.stos_brute as stos_brute
from nornir_imageregistration.transforms import *
from nornir_imageregistration.views import TransformWarpView
import nornir_pools
from nornir_shared import files, misc, plot, prettyoutput
from nornir_shared.processoutputinterceptor import ProcessOutputInterceptor, ProgressOutputInterceptor
import nornir_shared


class StomPreviewOutputInterceptor(ProgressOutputInterceptor):

    def __init__(self, proc, processData=None, OverlayFilename: str | None = None, DiffFilename: str | None = None, WarpedFilename: str | None = None):
        super(StomPreviewOutputInterceptor, self).__init__(proc, processData)

        self.Output = list()  # List of output lines
        self.LastLoadedFile = None  # Last file loaded by stom, used to rename the output
        self.stosfilename = None

        self.OverlayFilename = OverlayFilename
        self.DiffFilename = DiffFilename
        self.WarpedFilename = WarpedFilename
        return

    def Parse(self, line):
        '''Parse a line of output from stom so we can figure out how to correctly name the output files.
           sample input:
            Tool Percentage: 5.000000e-002
            loading 0009_ShadingCorrected-dapi_blob_1.png
            saving BruteResults/008.tif
            Tool Percentage: 5.000000e-002
            loading 0010_ShadingCorrected-dapi_blob_1.png
            saving BruteResults/009.tif
            Tool Percentage: 5.000000e-002'''

        # Line is called with None when the process has terminated which means it is safe to rename the created files
        if line is not None:
            # Let base class handle a progress percentage message
            ProgressOutputInterceptor.Parse(self, line)
            prettyoutput.Log(line)
            self.Output.append(line)
        else:
            outputfiles = list()
            '''Create a cmd for image magick to merge the images'''

            for line in self.Output:
                '''Processes a single line of output from the provided process and updates status as needed'''
                try:
                    line = line.lower()
                    if line.find("loading") >= 0:
                        parts = line.split()
                        [name, ext] = os.path.splitext(parts[1])
                        if self.stosfilename is None:
                            self.stosfilename = name
                        else:
                            self.LastLoadedFile = name

                    elif line.find("saving") >= 0:
                        parts = line.split()
                        outputFile = parts[1]
                        # Figure out if the output file has a different path
                        # path = os.path.dirname(outputFile)

                        [name, ext] = os.path.splitext(outputFile)
                        if ext is None:
                            ext = '.tif'

                        if len(ext) <= 0:
                            ext = '.tif'

                        outputfiles.append(outputFile)
                        # prettyoutput.Log("Renaming " + outputFile + " to " + os.path.join(path, self.LastLoadedFile + ext))

                        # shutil.move(outputFile, os.path.join(path, self.LastLoadedFile + ext))
                except:
                    pass

            if len(outputfiles) == 2:

                [OverlayFile, ext] = os.path.splitext(self.stosfilename)
                path = os.path.dirname(outputfiles[0])
                [temp, ext] = os.path.splitext(outputfiles[0])

                # Rename the files so we can continue without waiting for convert
                while True:
                    r = random.randrange(2, 100000, 1)
                    tempfilenameOne = os.path.join(path, str(r) + ext)
                    tempfilenameTwo = os.path.join(path, str(r + 1) + ext)

                    while os.path.exists(tempfilenameOne) or os.path.exists(tempfilenameTwo):
                        r = random.randrange(2, 100000, 1)
                        tempfilenameOne = os.path.join(path, str(r) + ext)
                        tempfilenameTwo = os.path.join(path, str(r + 1) + ext)

                    if (not os.path.exists(tempfilenameOne)) and (not os.path.exists(tempfilenameTwo)):
                        prettyoutput.Log("Renaming " + outputfiles[0] + " to " + tempfilenameOne)
                        shutil.move(outputfiles[0], tempfilenameOne)

                        prettyoutput.Log("Renaming " + outputfiles[1] + " to " + tempfilenameTwo)
                        shutil.move(outputfiles[1], tempfilenameTwo)
                        break

                if self.OverlayFilename is None:
                    OverlayFilename = 'overlay_' + OverlayFile.replace("temp", "", 1) + '.png'
                else:
                    OverlayFilename = self.OverlayFilename

                Pool = nornir_pools.GetGlobalProcessPool()

                cmd = 'magick convert -colorspace RGB ' + tempfilenameOne + ' ' + tempfilenameTwo + ' ' + tempfilenameOne + ' -combine -interlace PNG ' + OverlayFilename
                prettyoutput.Log(cmd)
                Pool.add_process(cmd, cmd + " && exit", shell=True)
                # subprocess.Popen(cmd + " && exit", shell=True)

                if self.DiffFilename is None:
                    DiffFilename = 'diff_' + OverlayFile.replace("temp", "", 1) + '.png'
                else:
                    DiffFilename = self.DiffFilename

                cmd = 'magick composite ' + tempfilenameOne + ' ' + tempfilenameTwo + ' -compose difference  -interlace PNG ' + DiffFilename
                prettyoutput.Log(cmd)

                Pool.add_process(cmd, cmd + " && exit", shell=True)

                if self.WarpedFilename is not None:
                    cmd = 'magick convert ' + tempfilenameTwo + " -interlace PNG " + self.WarpedFilename
                    Pool.add_process(cmd, cmd + " && exit", shell=True)

                # subprocess.call(cmd + " && exit", shell=True)
            else:
                prettyoutput.Log("Unexpected number of images output from ir-stom, expected 2: " + str(outputfiles))

        return


def SectionNumberKey(SectionNodeA) -> int:
    '''Sort section nodes by number'''
    return int(SectionNodeA.get('Number', None))


def SectionNumberCompare(SectionNodeA, SectionNodeB) -> int:
    '''Sort section nodes by number'''
    a = int(SectionNodeA.get('Number', None))
    b = int(SectionNodeB.get('Number', None))
    return (a > b) - (a < b)


def _GetCenterSection(Parameters, mapping_node: nornir_buildmanager.volumemanager.StosMapNode) -> int | None:
    '''Returns the number of the center section from the Block Node if possible, otherwise it checks the parameters.  Returns None if unspecified'''

    CenterSection = Parameters.get('CenterSection', None)
    try:
        CenterSection = int(CenterSection)
        return CenterSection
    except:
        CenterSection = None

    CenterSection = mapping_node.CenterSection
    if CenterSection is not None:
        return mapping_node.CenterSection

    return CenterSection


def UpdateStosMapWithRegistrationTree(StosMap: nornir_buildmanager.volumemanager.StosMapNode,
                                      RT: registrationtree.RegistrationTree, Mirror: bool, Logger):
    '''Adds any mappings missing in the StosMap with those from the registration tree
    @param StosMap StosMap: The Slice-to-slice mapping node to update
    @param registrationtree RT: The registration tree with new mappings
    @param bool Mirror: Remove all mappings that are not found in the registration tree
    @param Logger Logger: A logger class with an info method
     '''

    # Part one, add all RT mappings to the existing nodes
    Modified = False
    mappings = list(StosMap.Mappings)

    if Mirror:
        for m in mappings:
            StosMap.remove(m)
            Modified = True
    else:
        for mapping in mappings:
            control = mapping.Control

            rt_node = RT.Nodes.get(control, None)
            if not rt_node:
                Logger.info("Removing mapping missing from registration tree: " + str(mapping))
                StosMap.remove(mapping)
                Modified = True
                continue

            known_mappings = mapping.Mapped
            for rt_mapped in rt_node.Children:
                if rt_mapped.SectionNumber not in known_mappings:
                    Modified = True
                    mapping.AddMapping(rt_mapped.SectionNumber)

    # Part two, create nodes existing in the RT but not the StosMap
    for rt_node in list(RT.Nodes.values()):

        if len(rt_node.Children) == 0:
            continue

        known_mappings = StosMap.GetMappingsForControl(rt_node.SectionNumber)
        mappingNode = next(known_mappings, None)
        if mappingNode is None:
            # Create a mapping
            mappingNode = nornir_buildmanager.volumemanager.mappingnode.MappingNode.Create(rt_node.SectionNumber, None)
            StosMap.append(mappingNode)
            Logger.info("\tAdded Center %d" % rt_node.SectionNumber)
            Modified = True

        for rt_mapped in rt_node.Children:
            if rt_mapped.SectionNumber not in mappingNode.Mapped:
                mappingNode.AddMapping(rt_mapped.SectionNumber)
                Logger.info("\tAdded %d <- %d" % (rt_node.SectionNumber, rt_mapped.SectionNumber))
                Modified = True

    # Part three, remove nodes existing in the StosMap, but not the RT
    for mapping in StosMap.Mappings:
        control_section = mapping.Control
        if control_section not in RT.Nodes:
            StosMap.RemoveMapping(control_section)
            Logger.info(f"\tRemoved missing control section {control_section}")
            continue

        rt_node = RT.Nodes[control_section]
        rt_node_mapped_sections = frozenset(rt_node.ChildSectionNumbers)
        missing_sections = mapping.Mapped - rt_node_mapped_sections
        for missing_section in missing_sections:
            mapping.RemoveMapping(missing_section)
            Logger.info(f"\tRemoved mapping for missing mapped section {control_section} <- {missing_section}")

    return Modified


def CreateOrUpdateSectionToSectionMapping(Parameters,
                                          block_node: nornir_buildmanager.volumemanager.BlockNode,
                                          ChannelsRegEx: str | None,
                                          FiltersRegEx: str | None,
                                          Logger: logging.Logger | None,
                                          **kwargs):
    '''Figure out which sections should be registered to each other.
    Currently the only correct way to change the center section is to pass the center section
    to the align pipeline which forwards it to this function
        @BlockNode'''
    NumAdjacentSections = int(Parameters.get('NumAdjacentSections', '1'))
    StosMapName = Parameters.get('OutputStosMapName', 'PotentialRegistrationChain')  # type: str

    CenterSectionParameter = Parameters.get('CenterSection', None)
    try:
        CenterSectionParameter = int(CenterSectionParameter)
    except:
        CenterSectionParameter = None

    StosMapType = StosMapName + misc.GenNameFromDict(Parameters)

    SaveBlock = False
    CenterChanged = False
    SaveOutputMapping = False
    # Create a node to store the stos mappings
    OutputMappingNode = nornir_buildmanager.volumemanager.stosmapnode.StosMapNode.Create(Name=StosMapName,
                                                                                         Type=StosMapType)
    (NewStosMap, OutputMappingNode) = block_node.UpdateOrAddChildByAttrib(OutputMappingNode)

    SectionNodeList = list(block_node.Sections)
    SectionNodeList.sort(key=SectionNumberKey)

    # Ensure we do not have banned control sections in the output map
    NonStosSectionNumbersSet = block_node.NonStosSectionNumbers
    if not NewStosMap:
        existing_sections = [s.Number for s in SectionNodeList]
        removed_controls = OutputMappingNode.ClearMissingSections(existing_sections)
        removedControls = removed_controls or OutputMappingNode.ClearBannedControlMappings(NonStosSectionNumbersSet)
        if removedControls:
            SaveBlock = True

    # Add sections which do not have the correct channels or filters to the non-stos section list.  These will not be used as control sections
    MissingChannelOrFilterSections = [s for s in SectionNodeList if
                                      s.MatchChannelFilterPattern(ChannelsRegEx, FiltersRegEx) is False]
    MissingChannelOrFilterSectionNumbers = [s.SectionNumber for s in MissingChannelOrFilterSections]
    NonStosSectionNumbersSet = NonStosSectionNumbersSet.union(MissingChannelOrFilterSectionNumbers)

    # Identify the sections that can be control sections
    StosControlSectionNumbers = frozenset([SectionNumberKey(s) for s in SectionNodeList]).difference(
        NonStosSectionNumbersSet)

    adjusted_center_section = CenterSectionParameter
    if CenterSectionParameter in NonStosSectionNumbersSet:
        adjusted_center_section = registrationtree.NearestSection(StosControlSectionNumbers, CenterSectionParameter)
        Logger.warning(
            "Requested center section %1d was invalid.  Using %2d" % (CenterSectionParameter, adjusted_center_section))
        # CenterSectionParameter = adjusted_center_section  

    if not NewStosMap:
        if adjusted_center_section is None:
            adjusted_center_section = OutputMappingNode.CenterSection

        CenterChanged = adjusted_center_section != OutputMappingNode.CenterSection
        if CenterChanged:
            # We are changing the center section, so remove all of the section mappings
            Logger.warning(
                f"Requested center section {CenterSectionParameter} has changed from current value of {OutputMappingNode.CenterSection}.  Replacing existing mappings.")
            OutputMappingNode.CenterSection = adjusted_center_section
    else:
        if adjusted_center_section is not None:
            OutputMappingNode.CenterSection = adjusted_center_section

    # CenterSectionNumber = adjusted_center_section#_GetCenterSection(Parameters, OutputMappingNode)

    DefaultRT = registrationtree.RegistrationTree.CreateRegistrationTree(StosControlSectionNumbers,
                                                                         adjacentThreshold=NumAdjacentSections,
                                                                         center=adjusted_center_section)
    DefaultRT.AddNonControlSections(block_node.NonStosSectionNumbers, center=adjusted_center_section)

    if DefaultRT.IsEmpty:
        return None

    if OutputMappingNode.CenterSection is None:
        OutputMappingNode.CenterSection = list(DefaultRT.RootNodes.values())[0].SectionNumber

    if OutputMappingNode.ClearBannedControlMappings(NonStosSectionNumbersSet):
        SaveOutputMapping = True

    if UpdateStosMapWithRegistrationTree(OutputMappingNode, DefaultRT, CenterChanged, Logger):
        SaveOutputMapping = True

    SaveBlock = SaveBlock or NewStosMap

    if SaveBlock:
        return block_node
    elif SaveOutputMapping:
        # Cannot save OutputMapping, it is not a container
        return block_node

    return None


def __CallNornirStosBrute(stosNode, Downsample, ControlImageFullPath: str, MappedImageFullPath: str,
                          ControlMaskImageFullPath: str | None = None, MappedMaskImageFullPath: str | None = None,
                          AngleSearchRange: list[float] | None = None, TestForFlip: bool = True,
                          WarpedImageScaleFactors=None,
                          argstring=None, Logger=None):
    '''Call the stos-brute version from nornir-imageregistration'''

    alignment = stos_brute.SliceToSliceBruteForce(FixedImageInput=ControlImageFullPath,
                                                  WarpedImageInput=MappedImageFullPath,
                                                  FixedImageMaskPath=ControlMaskImageFullPath,
                                                  WarpedImageMaskPath=MappedMaskImageFullPath,
                                                  AngleSearchRange=AngleSearchRange,
                                                  TestFlip=TestForFlip,
                                                  WarpedImageScaleFactors=WarpedImageScaleFactors,
                                                  Cluster=False)

    # Close pools to prevent threads from sticking around and slowing the rest of the run
    nornir_pools.ClosePools()

    stos = alignment.ToStos(ControlImageFullPath,
                            MappedImageFullPath,
                            ControlMaskImageFullPath,
                            MappedMaskImageFullPath,
                            PixelSpacing=Downsample)

    stos.Save(stosNode.FullPath)

    return


def __CallIrToolsStosBrute(stosNode, ControlImageNode: ImageNode, MappedImageNode: ImageNode, ControlMaskImageNode: ImageNode | None = None,
                           MappedMaskImageNode: ImageNode | None = None, argstring: str | None = None, Logger=None):
    if argstring is None:
        argstring = ""

    StosBruteTemplate = 'ir-stos-brute ' + argstring + '-save %(OutputFile)s -load %(ControlImage)s %(MovingImage)s -mask %(ControlMask)s %(MovingMask)s'
    StosBruteTemplateNoMask = 'ir-stos-brute ' + argstring + '-save %(OutputFile)s -load %(ControlImage)s %(MovingImage)s '

    cmd = None
    if not (ControlMaskImageNode is None or MappedMaskImageNode is None):
        cmd = StosBruteTemplate % {'OutputFile': stosNode.FullPath,
                                   'ControlImage': ControlImageNode.FullPath,
                                   'MovingImage': MappedImageNode.FullPath,
                                   'ControlMask': ControlMaskImageNode.FullPath,
                                   'MovingMask': MappedMaskImageNode.FullPath}
    else:
        cmd = StosBruteTemplateNoMask % {'OutputFile': stosNode.FullPath,
                                         'ControlImage': ControlImageNode.FullPath,
                                         'MovingImage': MappedImageNode.FullPath}

    prettyoutput.Log(cmd)
    subprocess.call(cmd + " && exit", shell=True)

    CmdRan = True

    if not os.path.exists(stosNode.FullPath):
        Logger.error("Stos brute did not produce useable output\n" + cmd)
        return None


def GetOrCreateRegistrationImageNodes(filter_node: nornir_buildmanager.volumemanager.FilterNode, Downsample: float,
                                      GetMask: bool, Logger=None):
    '''
    :param Logger:
    :param object filter_node: Filter meta-data to get images for
    :param int Downsample: Resolution of the image node to fetch or create
    :param bool GetMask: True if the mask node should be returned
    :return: Tuple of (image_node, mask_node) for the filter at the given downsample level
    '''
    if Logger is None:
        Logger = logging.getLogger(__name__ + ".FilterToFilterBruteRegistration")

    image_node = filter_node.GetOrCreateImage(Downsample)
    if image_node is None:
        Logger.error("Image metadata missing %s" % filter_node.FullPath)
        return None, None

    if not os.path.exists(image_node.FullPath):
        Logger.error("Image image file missing %s" % image_node.FullPath)
        return None, None

    mask_image_node = None
    if GetMask:
        mask_image_node = filter_node.GetOrCreateMaskImage(Downsample)
        if mask_image_node is None:
            Logger.error("Mask image metadata missing %s" % filter_node.FullPath)
            return None, None

        if not os.path.exists(mask_image_node.FullPath):
            Logger.error("Mask image file missing %s" % mask_image_node.FullPath)
            return None, None

    return image_node, mask_image_node


def _CalculateFilterToFilterBruteRegistrationScaleFactor(ControlFilter: nornir_buildmanager.volumemanager.FilterNode,
                                                         MappedFilter: nornir_buildmanager.volumemanager.FilterNode):
    '''Given two filters, determines if scale data is available.  If they are
       calculates how much to scale the mapped image to ensure it is at the 
       same scale as the ControlFilter.
       '''

    ControlScale = ControlFilter.Scale
    MappedScale = MappedFilter.Scale

    if ControlScale is None or MappedScale is None:
        return None

    x_scale = ControlScale.X / MappedScale.X
    y_scale = ControlScale.Y / MappedScale.Y

    return x_scale, y_scale


def FilterToFilterBruteRegistration(StosGroup: nornir_buildmanager.volumemanager.StosGroupNode,
                                    ControlFilter: nornir_buildmanager.volumemanager.FilterNode,
                                    MappedFilter: nornir_buildmanager.volumemanager.FilterNode,
                                    OutputType: str,
                                    OutputPath: str,
                                    UseMasks: bool,
                                    AngleSearchRange=None,
                                    TestForFlip=True,
                                    Logger=None,
                                    argstring=None):
    '''Create a transform node, populate, and generate the transform'''
    CmdRan = False
    ManualFileExists = False

    if Logger is None:
        Logger = logging.getLogger(__name__ + ".FilterToFilterBruteRegistration")

    stosNode = StosGroup.GetStosTransformNode(ControlFilter, MappedFilter)
    if stosNode is not None:
        if StosGroup.AreStosInputImagesOutdated(stosNode, ControlFilter, MappedFilter, MaskRequired=UseMasks):
            stosNode.Clean("Input Images are Outdated")
            stosNode = None
        else:
            # Check if the manual stos file exists and is different than the output file        
            ManualStosFileFullPath = StosGroup.PathToManualTransform(stosNode.FullPath)
            ManualFileExists = ManualStosFileFullPath is not None
            ManualInputChecksum = None
            if ManualFileExists:
                if 'InputTransformChecksum' in stosNode.attrib:
                    ManualInputChecksum = stosfile.StosFile.LoadChecksum(ManualStosFileFullPath)
                    stosNode = transforms.RemoveOnMismatch(stosNode, 'InputTransformChecksum', ManualInputChecksum)
                else:
                    stosNode.Clean(
                        "No input checksum to test manual stos file against. Replacing with new manual input")
                    stosNode = None

            if not ManualFileExists:
                if 'InputTransformChecksum' in stosNode.attrib:
                    stosNode.Clean("Manual file used to create transform but the manual file has been removed")
                    stosNode = None

    # Get or create the input images
    try:
        (ControlImageNode, ControlMaskImageNode) = GetOrCreateRegistrationImageNodes(ControlFilter,
                                                                                     StosGroup.Downsample,
                                                                                     GetMask=UseMasks, Logger=Logger)
        (MappedImageNode, MappedMaskImageNode) = GetOrCreateRegistrationImageNodes(MappedFilter, StosGroup.Downsample,
                                                                                   GetMask=UseMasks, Logger=Logger)
    except NornirUserException as e:
        prettyoutput.LogErr(str(e))
        return None

    if stosNode is None:
        stosNode = StosGroup.CreateStosTransformNode(ControlFilter, MappedFilter, OutputType, OutputPath)

        # We just created this, so remove any old files
        if os.path.exists(stosNode.FullPath):
            # Uncomment to leave the file in place and update the meta-data
            #             stosNode.ResetChecksum()
            #
            #             if ManualFileExists:
            #                 stosNode.InputTransformChecksum = stosfile.StosFile.LoadChecksum(ManualStosFileFullPath)
            #
            #             StosGroup.AddChecksumsToStos(stosNode, ControlFilter, MappedFilter)
            #             return stosNode
            os.remove(stosNode.FullPath)

    # print OutputFileFullPath

    if not os.path.exists(stosNode.FullPath):
        ManualStosFileFullPath = StosGroup.PathToManualTransform(stosNode.FullPath)
        if ManualStosFileFullPath:
            prettyoutput.Log("Copy manual override stos file to output: " + os.path.basename(ManualStosFileFullPath))
            shutil.copy(ManualStosFileFullPath, stosNode.FullPath)
            # Ensure we add or remove masks according to the parameters
            SetStosFileMasks(stosNode.FullPath, ControlFilter, MappedFilter, UseMasks, StosGroup.Downsample)
            ManualInputChecksum = stosfile.StosFile.LoadChecksum(ManualStosFileFullPath)

            stosNode.InputTransformChecksum = ManualInputChecksum
        else:
            # Calculate if both images have the same scale and adjust if needed
            _CalculateFilterToFilterBruteRegistrationScaleFactor(ControlFilter, MappedFilter)
            if not (ControlMaskImageNode is None and MappedMaskImageNode is None):
                __CallNornirStosBrute(stosNode, StosGroup.Downsample,
                                      ControlImageNode.FullPath, MappedImageNode.FullPath,
                                      ControlMaskImageNode.FullPath, MappedMaskImageNode.FullPath,
                                      AngleSearchRange=AngleSearchRange, TestForFlip=TestForFlip)
            else:
                __CallNornirStosBrute(stosNode, StosGroup.Downsample,
                                      ControlImageNode.FullPath, MappedImageNode.FullPath,
                                      AngleSearchRange=AngleSearchRange, TestForFlip=TestForFlip)

        CmdRan = True
        # __CallIrToolsStosBrute(stosNode, ControlImageNode, MappedImageNode, ControlMaskImageNode, MappedMaskImageNode, argstring, Logger)

        # Rescale stos file to full-res
        # stosFile = stosfile.StosFile.Load(stosNode.FullPath)
        # stosFile.Scale(StosGroup.Downsample)
        # stosFile.Save(stosNode.FullPath)

        # Load and save the stos file to ensure the transform doesn't have the original Ir-Tools floating point string representation which
        # have identical values but different checksums from the Python stos file objects %g representation

        # stosNode.Checksum = stosfile.StosFile.LoadChecksum(stosNode.FullPath)
        stosNode.ResetChecksum()
        StosGroup.AddChecksumsToStos(stosNode, ControlFilter, MappedFilter)

    if CmdRan:
        return stosNode

    return


def StosBrute(Parameters, mapping_node: MappingNode, block_node: BlockNode, ChannelsRegEx: str, FiltersRegEx: str, Logger, **kwargs):
    '''Create an initial rotation and translation alignment for a pair of unregistered images'''

    Downsample = int(Parameters.get('Downsample', 32))
    OutputStosGroupName = kwargs.get('OutputGroup', 'Brute')
    OutputStosType = kwargs.get('Type', 'Brute')
    AngleSearchRange = kwargs.get('AngleSearchRange', None)
    NoFlipCheck = bool(kwargs.get('NoFlipCheck', False))

    TestForFlip = not NoFlipCheck

    # Argparse value for 
    if AngleSearchRange == "None":
        AngleSearchRange = None

    # Additional arguments for stos-brute
    argstring = misc.ArgumentsFromDict(Parameters)

    UseMasks = Parameters.get("UseMasks", False)

    ControlNumber = mapping_node.Control
    AdjacentSections = mapping_node.Mapped

    # Find the nodes for the control and mapped sections
    ControlSectionNode = block_node.GetSection(ControlNumber)
    if ControlSectionNode is None:
        Logger.error("Missing control section node for # " + str(ControlNumber))
        return

    os.makedirs(OutputStosGroupName, exist_ok=True)

    (added, stos_group_node) = block_node.GetOrCreateStosGroup(OutputStosGroupName, downsample=Downsample)
    stos_group_node.CreateDirectories()
    if added:
        (yield block_node)

    os.makedirs(stos_group_node.FullPath, exist_ok=True)

    for MappedSection in AdjacentSections:
        mapped_section_node = block_node.GetSection(MappedSection)

        if mapped_section_node is None:
            prettyoutput.LogErr("Could not find expected section for StosBrute: " + str(MappedSection))
            continue

        # Figure out all the combinations of assembled images between the two section and test them
        MappedFilterList = mapped_section_node.MatchChannelFilterPattern(ChannelsRegEx, FiltersRegEx)

        if 'Downsample' in Parameters:
            del Parameters['Downsample']

        for MappedFilter in MappedFilterList:
            print("\tMap - " + MappedFilter.FullPath)

            ControlFilterList = ControlSectionNode.MatchChannelFilterPattern(ChannelsRegEx, FiltersRegEx)
            for ControlFilter in ControlFilterList:
                print("\tCtrl - " + ControlFilter.FullPath)

                OutputFile = nornir_buildmanager.volumemanager.stosgroupnode.StosGroupNode.GenerateStosFilename(
                    ControlFilter, MappedFilter)

                (added, stos_mapping_node) = stos_group_node.GetOrCreateSectionMapping(MappedSection)
                if added:
                    (yield stos_mapping_node.Parent)

                stosNode = FilterToFilterBruteRegistration(StosGroup=stos_group_node,
                                                           ControlFilter=ControlFilter,
                                                           MappedFilter=MappedFilter,
                                                           OutputType=OutputStosType,
                                                           OutputPath=OutputFile,
                                                           UseMasks=UseMasks,
                                                           AngleSearchRange=AngleSearchRange,
                                                           TestForFlip=TestForFlip
                                                           )

                if stosNode is not None:
                    (yield stosNode.Parent)

    return


def GetImage(block_node: BlockNode, SectionNumber: int, Channel: str, Filter: str, Downsample: int) \
        -> tuple[ImageNode | None, ImageNode | None]:
    '''Will raise a NornirUserException if the image cannot be generated'''

    sectionNode = block_node.GetSection(SectionNumber)
    if sectionNode is None:
        return None, None

    channelNode = sectionNode.GetChannel(Channel)
    if channelNode is None:
        return None, None

    filterNode = channelNode.GetFilter(Filter)
    if filterNode is None:
        return None, None

    return filterNode.GetOrCreateImage(Downsample), filterNode.GetMaskImage(Downsample)


class StosImageNodesOutput(typing.NamedTuple):
    ControlImageNode: ImageNode
    ControlImageMaskNode: ImageNode | None
    MappedImageNode: ImageNode
    MappedImageMaskNode: ImageNode | None


def StosImageNodes(StosTransformNode: TransformNode, Downsample: int) -> StosImageNodesOutput:
    block_node = StosTransformNode.FindParent('Block')  # type: BlockNode | None

    (ControlImageNode, ControlImageMaskNode) = GetImage(block_node,
                                                        SectionNumber=StosTransformNode.ControlSectionNumber,
                                                        Channel=StosTransformNode.ControlChannelName,
                                                        Filter=StosTransformNode.ControlFilterName,
                                                        Downsample=Downsample)

    (MappedImageNode, MappedImageMaskNode) = GetImage(block_node,
                                                      SectionNumber=StosTransformNode.MappedSectionNumber,
                                                      Channel=StosTransformNode.MappedChannelName,
                                                      Filter=StosTransformNode.MappedFilterName,
                                                      Downsample=Downsample)

    return StosImageNodesOutput(ControlImageNode=ControlImageNode, ControlImageMaskNode=ControlImageMaskNode,
                                MappedImageNode=MappedImageNode, MappedImageMaskNode=MappedImageMaskNode)


def ValidateSectionMappingPipeline(Parameters, Logger, section_mapping_node: SectionMappingsNode, **kwargs) \
        -> XElementWrapper | None:
    return ValidateSectionMapping(section_mapping_node, Logger)


def ValidateSectionMapping(section_mapping_node: SectionMappingsNode, Logger) -> XElementWrapper | None:
    save_node = False
    cleaned, reason = section_mapping_node.CleanIfInvalid()
    save_node |= cleaned
    for t in section_mapping_node.Transforms:
        save_node |= ValidateSectionMappingTransform(t, Logger) is not None

    for img in section_mapping_node.Images:
        cleaned, reason = img.CleanIfInvalid()
        save_node |= cleaned

    if save_node:
        return section_mapping_node

    return None


def ValidateSectionMappingTransformPipeline(Parameters, Logger, stos_transform_node: TransformNode, **kwargs) \
        -> XElementWrapper | None:
    return ValidateSectionMappingTransform(stos_transform_node, Logger)


def ValidateSectionMappingTransform(stos_transform_node: TransformNode, Logger) -> XElementWrapper | None:
    parent = stos_transform_node.Parent
    stos_group = stos_transform_node.FindParent('StosGroup')
    downsample = int(stos_group.Downsample)
    (mapped_filter, mapped_mask_filter) = __MappedFilterForTransform(stos_transform_node)
    (control_filter, control_mask_filter) = __ControlFilterForTransform(stos_transform_node)

    if mapped_filter is None or control_filter is None:
        Logger.warn("Removed stos file for missing filters: %s" % stos_transform_node.FullPath)
        parent.remove(stos_transform_node)
        return parent

    # Could be a generated transform not pointing at actual images, move on if the input image does not exist at that level 
    if control_filter.GetImage(downsample) is None:
        return None
    if mapped_filter.GetImage(downsample) is None:
        return None

    if FixStosFilePaths(control_filter, mapped_filter, stos_transform_node, downsample):
        Logger.warn("Updated stos images: %s" % stos_transform_node.FullPath)
        return parent

    return None


def UpdateStosImagePaths(StosTransformPath: str, ControlImageFullPath: str, MappedImageFullPath: str,
                         ControlImageMaskFullPath: str | None = None,
                         MappedImageMaskFullPath: str | None = None) -> bool:
    '''
    Replace the paths of the stos file with the passed parameters
    :return: True if the stos file was updated
    '''

    # ir-stom's -slice_dirs argument is broken for masks, so we have to patch the stos file before use
    InputStos = stosfile.StosFile.Load(StosTransformPath)

    NeedsUpdate = InputStos.ControlImageFullPath != ControlImageFullPath or \
                  InputStos.MappedImageFullPath != MappedImageFullPath or \
                  InputStos.ControlMaskFullPath != ControlImageMaskFullPath or \
                  InputStos.MappedMaskFullPath != MappedImageMaskFullPath

    if NeedsUpdate:
        InputStos.ControlImageFullPath = ControlImageFullPath
        InputStos.MappedImageFullPath = MappedImageFullPath

        if InputStos.ControlMaskName is not None:
            InputStos.ControlMaskFullPath = ControlImageMaskFullPath

        if InputStos.MappedMaskName is not None:
            InputStos.MappedMaskFullPath = MappedImageMaskFullPath

        InputStos.Save(StosTransformPath)

    return NeedsUpdate


def FixStosFilePaths(ControlFilter: FilterNode, MappedFilter: FilterNode, StosTransformNode: TransformNode,
                     Downsample: int, StosFilePath: str | None = None):
    '''Check if the stos file uses appropriate images for the passed filters'''

    if StosFilePath is None:
        StosFilePath = StosTransformNode.FullPath

    if ControlFilter.GetMaskImage(Downsample) is None or MappedFilter.GetMaskImage(Downsample) is None:
        return UpdateStosImagePaths(StosFilePath,
                                    ControlFilter.GetImage(Downsample).FullPath,
                                    MappedFilter.GetImage(Downsample).FullPath)
    else:
        return UpdateStosImagePaths(StosFilePath,
                                    ControlFilter.GetImage(Downsample).FullPath,
                                    MappedFilter.GetImage(Downsample).FullPath,
                                    ControlFilter.GetMaskImage(Downsample).FullPath,
                                    MappedFilter.GetMaskImage(Downsample).FullPath)


def SectionToVolumeImage(Parameters, transform_node: TransformNode, Logger, CropUndefined: bool = True,
                         **kwargs) -> XElementWrapper | None:
    '''Executre ir-stom on a provided .stos file'''

    GroupNode = transform_node.FindParent("StosGroup")
    SaveRequired = False

    SectionMappingNode = transform_node.FindParent('SectionMappings')

    FilePrefix = str(SectionMappingNode.MappedSectionNumber) + '-' + str(transform_node.ControlSectionNumber) + '_'
    WarpedOutputFilename = FilePrefix + 'warped_' + GroupNode.Name + "_" + transform_node.Type + '.png'
    WarpedOutputFileFullPath = os.path.join(GroupNode.FullPath, WarpedOutputFilename)

    # Create a node in the XML records

    (created, WarpedImageNode) = GetOrCreateImageNodeHelper(SectionMappingNode, WarpedOutputFileFullPath)
    WarpedImageNode.Type = 'Warped_' + transform_node.Type

    stosImages = StosImageNodes(transform_node, GroupNode.Downsample)

    # Compare the .stos file creation date to the output

    WarpedImageNode = transforms.RemoveOnMismatch(WarpedImageNode, 'InputTransformChecksum', transform_node.Checksum)

    if WarpedImageNode is not None:
        files.RemoveOutdatedFile(stosImages.ControlImageNode.FullPath, WarpedImageNode.FullPath)
        files.RemoveOutdatedFile(stosImages.MappedImageNode.FullPath, WarpedImageNode.FullPath)
    else:
        (created, WarpedImageNode) = GetOrCreateImageNodeHelper(SectionMappingNode, WarpedOutputFileFullPath)
        WarpedImageNode.Type = 'Warped_' + transform_node.Type

    if not os.path.exists(WarpedImageNode.FullPath):
        SaveRequired = True
        WarpedImageNode.InputTransformChecksum = transform_node.Checksum
        assemble.TransformStos(transform_node.FullPath, OutputFilename=WarpedImageNode.FullPath,
                               CropUndefined=CropUndefined)
        prettyoutput.Log("Saving image: " + WarpedImageNode.FullPath)

    if SaveRequired:
        return GroupNode
    else:
        return None


def AssembleStosOverlays(Parameters,
                         stos_map_node: nornir_buildmanager.volumemanager.StosMapNode,
                         group_node: nornir_buildmanager.volumemanager.StosGroupNode,
                         Logger, **kwargs) -> XElementWrapper | None:
    '''Executre ir-stom on a provided .stos file'''

    oldDir = os.getcwd()
    # TransformXPathTemplate = "SectionMappings[@MappedSectionNumber='%(MappedSection)d']/Transform[@ControlSectionNumber='%(ControlSection)d']"

    SectionMappingSaveRequired = False

    tempdir = tempfile.mkdtemp() + os.path.sep

    try:
        for mapping_node in stos_map_node.Mappings:
            MappedSectionList = mapping_node.Mapped

            for MappedSection in MappedSectionList:
                # Find the inputTransformNode in the InputGroupNode
                # TransformXPath = TransformXPathTemplate % {'MappedSection' : MappedSection,
                #                                           'ControlSection' : MappingNode.Control}

                # StosTransformNode = GroupNode.find(TransformXPath)

                StosTransformNodes = group_node.TransformsForMapping(MappedSection, mapping_node.Control)
                if StosTransformNodes is None:
                    Logger.warn(
                        "No transform found for mapping: " + str(MappedSection) + " -> " + str(mapping_node.Control))
                    continue

                for StosTransformNode in StosTransformNodes:
                    SectionMappingNode = StosTransformNode.FindParent('SectionMappings')
                    [TransformBaseFilename, ext] = os.path.splitext(StosTransformNode.Path)
                    OverlayOutputFilename = 'overlay_' + TransformBaseFilename + '.png'
                    DiffOutputFilename = 'diff_' + TransformBaseFilename + '.png'
                    WarpedOutputFilename = 'warped_' + TransformBaseFilename + '.png'

                    OverlayOutputFileFullPath = os.path.join(group_node.FullPath, OverlayOutputFilename)
                    DiffOutputFileFullPath = os.path.join(group_node.FullPath, DiffOutputFilename)
                    WarpedOutputFileFullPath = os.path.join(group_node.FullPath, WarpedOutputFilename)

                    os.chdir(group_node.FullPath)

                    os.makedirs('Temp', exist_ok=True)

                    # Create a node in the XML records
                    (created_overlay, OverlayImageNode) = GetOrCreateImageNodeHelper(SectionMappingNode,
                                                                                     OverlayOutputFileFullPath)
                    OverlayImageNode.Type = 'Overlay_' + StosTransformNode.Type
                    (created_diff, DiffImageNode) = GetOrCreateImageNodeHelper(SectionMappingNode,
                                                                               DiffOutputFileFullPath)
                    DiffImageNode.Type = 'Diff_' + StosTransformNode.Type
                    (created_warped, WarpedImageNode) = GetOrCreateImageNodeHelper(SectionMappingNode,
                                                                                   WarpedOutputFileFullPath)
                    WarpedImageNode.Type = 'Warped_' + StosTransformNode.Type

                    SectionMappingSaveRequired = SectionMappingSaveRequired or created_overlay or created_diff or created_warped

                    stosImages = StosImageNodes(StosTransformNode, group_node.Downsample)

                    if stosImages.ControlImageNode is None or stosImages.MappedImageNode is None:
                        continue

                    if created_overlay:
                        OverlayImageNode.SetTransform(StosTransformNode)
                    else:
                        if not OverlayImageNode.CleanIfInputTransformMismatched(StosTransformNode):
                            files.RemoveOutdatedFile(StosTransformNode.FullPath, OverlayImageNode.FullPath)
                            files.RemoveOutdatedFile(stosImages.ControlImageNode.FullPath, OverlayImageNode.FullPath)
                            files.RemoveOutdatedFile(stosImages.MappedImageNode.FullPath, OverlayImageNode.FullPath)

                    if created_diff:
                        DiffImageNode.SetTransform(StosTransformNode)
                    else:
                        DiffImageNode.CleanIfInputTransformMismatched(StosTransformNode)

                    if created_warped:
                        WarpedImageNode.SetTransform(StosTransformNode)
                    else:
                        WarpedImageNode.CleanIfInputTransformMismatched(StosTransformNode)

                    # Compare the .stos file creation date to the output

                    # ===========================================================
                    # if hasattr(OverlayImageNode, 'InputTransformChecksum'):
                    #     transforms.RemoveOnMismatch(OverlayImageNode, 'InputTransformChecksum', StosTransformNode.Checksum)
                    # if hasattr(DiffImageNode, 'InputTransformChecksum'):
                    #     transforms.RemoveOnMismatch(DiffImageNode, 'InputTransformChecksum', StosTransformNode.Checksum)
                    # if hasattr(WarpedImageNode, 'InputTransformChecksum'):
                    #     transforms.RemoveOnMismatch(WarpedImageNode, 'InputTransformChecksum', StosTransformNode.Checksum)
                    # ===========================================================

                    if not (os.path.exists(OverlayImageNode.FullPath) and os.path.exists(
                            DiffImageNode.FullPath) and os.path.exists(WarpedImageNode.FullPath)):

                        # ir-stom's -slice_dirs argument is broken for masks, so we have to patch the stos file before use
                        if stosImages.ControlImageMaskNode is None or stosImages.MappedImageMaskNode is None:
                            UpdateStosImagePaths(StosTransformNode.FullPath,
                                                 stosImages.ControlImageNode.FullPath,
                                                 stosImages.MappedImageNode.FullPath, )
                        else:
                            UpdateStosImagePaths(StosTransformNode.FullPath,
                                                 stosImages.ControlImageNode.FullPath,
                                                 stosImages.MappedImageNode.FullPath,
                                                 stosImages.ControlImageMaskNode.FullPath,
                                                 stosImages.MappedImageMaskNode.FullPath)

                        cmd = f'ir-stom -load {StosTransformNode.FullPath} -save {tempdir} ' + misc.ArgumentsFromDict(
                            Parameters)

                        NewP = subprocess.Popen(cmd + " && exit", shell=True, stdout=subprocess.PIPE)
                        ProcessOutputInterceptor.Intercept(StomPreviewOutputInterceptor(NewP,
                                                                                        OverlayFilename=OverlayImageNode.FullPath,
                                                                                        DiffFilename=DiffImageNode.FullPath,
                                                                                        WarpedFilename=WarpedImageNode.FullPath))

                        SectionMappingSaveRequired = True

                        OverlayImageNode.SetTransform(StosTransformNode)
                        DiffImageNode.SetTransform(StosTransformNode)
                        WarpedImageNode.SetTransform(StosTransformNode)

            # Figure out where our output should live...

            # try:
            # shutil.rmtree('Temp')
            # except:
            # pass

        # Pool = nornir_pools.GetGlobalProcessPool()
        # Pool.wait_completion()
        nornir_pools.WaitOnAllPools()
    finally:
        files.rmtree(tempdir, ignore_errors=True)

        os.chdir(oldDir)

    if SectionMappingSaveRequired:
        return group_node

    return None


def CalculateStosGroupWarpMeasurementImages(Parameters, stos_map_node: StosMapNode, group_node: StosGroupNode, Logger,
                                            **kwargs) -> XElementWrapper:
    ''''Execute ir-stom on a provided .stos file'''

    maxReportedAngle = kwargs.get('MaxReportedAngle', None)
    RenderToSourceSpace = kwargs.get('RenderToSourceSpace', True)

    # oldDir = os.getcwd()
    TransformXPathTemplate = "SectionMappings[@MappedSectionNumber='%(MappedSection)d']/Transform[@ControlSectionNumber='%(ControlSection)d']"

    SaveRequired = False
    block_node = stos_map_node.FindParent('Block')

    for mapping_node in stos_map_node.findall('Mapping'):
        MappedSectionList = mapping_node.Mapped

        for MappedSection in MappedSectionList:
            # Find the inputTransformNode in the InputGroupNode
            # TransformXPath = TransformXPathTemplate % {'MappedSection' : MappedSection,
            #                                           'ControlSection' : mapping_node.Control}

            # StosTransformNode = GroupNode.find(TransformXPath)
            StosTransformNodes = group_node.TransformsForMapping(MappedSection, mapping_node.Control)
            if StosTransformNodes is None:
                Logger.warn(
                    "No transform found for mapping: " + str(MappedSection) + " -> " + str(mapping_node.Control))
                continue

            for StosTransformNode in StosTransformNodes:

                if not os.path.exists(StosTransformNode.FullPath):
                    continue

                SectionMappingNode = StosTransformNode.FindParent('SectionMappings')
                [TransformBaseFilename, ext] = os.path.splitext(StosTransformNode.Path)
                WarpImageOutputFilename = 'warp_' + TransformBaseFilename + '.png'
                HistogramOutputFilename = 'warpHistogram_' + TransformBaseFilename + '.xml'
                HistogramImageOutputFilename = 'warpHistogram_' + TransformBaseFilename + '.png'

                WarpImageOutputFileFullPath = os.path.join(group_node.FullPath, WarpImageOutputFilename)
                HistogramOutputFileFullPath = os.path.join(group_node.FullPath, HistogramOutputFilename)

                # Create a node in the XML records
                (created_warp, WarpImageNode) = GetOrCreateImageNodeHelper(SectionMappingNode,
                                                                           WarpImageOutputFileFullPath)
                WarpImageNode.Type = 'WarpMetricImage_' + StosTransformNode.Type
                #                 (created_histogram_image, WarpHistogramImageNode) = GetOrCreateImageNodeHelper(SectionMappingNode, HistogramImageOutputFileFullPath, StosTransformNode)
                #                 WarpHistogramImageNode.Type = 'WarpHistogram_' + StosTransformNode.Type
                (created_histogram, WarpHistogramNode) = GetOrCreateHistogramNodeHelper(SectionMappingNode,
                                                                                        HistogramOutputFilename,
                                                                                        HistogramImageOutputFilename,
                                                                                        StosTransformNode,
                                                                                        Type=f'WarpHistogram_{StosTransformNode.Type}')

                if created_warp:
                    WarpImageNode.SetTransform(StosTransformNode)
                else:
                    if WarpImageNode.CleanIfInputTransformMismatched(StosTransformNode):
                        files.RemoveOutdatedFile(StosTransformNode.FullPath, WarpImageNode.FullPath)

                #                 if created_histogram_image:
                #                     WarpHistogramImageNode.SetTransform(StosTransformNode)

                # Compare the .stos file creation date to the output

                # ===========================================================
                # if hasattr(OverlayImageNode, 'InputTransformChecksum'):
                #     transforms.RemoveOnMismatch(OverlayImageNode, 'InputTransformChecksum', StosTransformNode.Checksum)
                # if hasattr(DiffImageNode, 'InputTransformChecksum'):
                #     transforms.RemoveOnMismatch(DiffImageNode, 'InputTransformChecksum', StosTransformNode.Checksum)
                # if hasattr(WarpedImageNode, 'InputTransformChecksum'):
                #     transforms.RemoveOnMismatch(WarpedImageNode, 'InputTransformChecksum', StosTransformNode.Checksum)
                # ===========================================================

                twarpView = None

                if not os.path.exists(WarpImageNode.FullPath):

                    if twarpView is None:
                        twarpView = TransformWarpView(StosTransformNode.FullPath)

                    twarpView.GenerateWarpImage(outputfullpath=WarpImageNode.FullPath,
                                                RenderToSourceSpace=RenderToSourceSpace,
                                                title=str(MappedSection) + " -> " + str(mapping_node.Control),
                                                maxAngle=maxReportedAngle)
                    WarpImageNode.SetTransform(StosTransformNode)
                    SaveRequired = True

                if not (os.path.exists(WarpHistogramNode.DataFullPath) and os.path.exists(
                        WarpHistogramNode.ImageFullPath)):

                    if twarpView is None:
                        twarpView = TransformWarpView(StosTransformNode.FullPath)

                    h = twarpView.GenerateWarpHistogram(WarpHistogramNode.DataFullPath)
                    h.Save(WarpHistogramNode.DataFullPath)

                    plot.Histogram(h, WarpHistogramNode.ImageFullPath,
                                   Title=str(MappedSection) + " -> " + str(mapping_node.Control), xlabel='Angle Delta')

                    WarpHistogramNode.SetTransform(StosTransformNode)
                    # WarpHistogramImageNode.SetTransform(StosTransformNode) 
                    SaveRequired = True

        # Figure out where our output should live...

        # try:
        # shutil.rmtree('Temp')
        # except:
        # pass

    if SaveRequired:
        return block_node
    else:
        return group_node


def SelectBestRegistrationChain(Parameters, InputGroupNode: nornir_buildmanager.volumemanager.StosGroupNode,
                                InputStosMapNode: nornir_buildmanager.volumemanager.StosMapNode,
                                OutputStosMapName: str, Logger, **kwargs):
    '''Figure out which sections should be registered to each other'''
    Pool = None
    # Assess all of the images
    ComparisonImageType = kwargs.get('ComparisonImageType', 'Diff_Brute')
    ImageSearchXPathTemplate = "Image[@InputTransformChecksum='%(InputTransformChecksum)s']"

    # OutputStosMapName = kwargs.get('OutputStosMapName', 'FinalStosMap')

    block_node = InputGroupNode.FindParent('Block')

    # OK, we have the best mapping. Add it to our registration chain.
    # Create a node to store the stos mappings
    OutputStosMapNode = nornir_buildmanager.volumemanager.stosmapnode.StosMapNode.Create(Name=OutputStosMapName,
                                                                                         Type=OutputStosMapName,
                                                                                         CenterSection=str(
                                                                                             InputStosMapNode.CenterSection))
    (NewStosMap, OutputStosMapNode) = block_node.UpdateOrAddChildByAttrib(OutputStosMapNode)

    # Ensure we do not have banned control sections in the output map
    if not NewStosMap:
        NonStosSectionNumbersSet = block_node.NonStosSectionNumbers
        removedControls = OutputStosMapNode.ClearBannedControlMappings(NonStosSectionNumbersSet)
        if removedControls:
            yield block_node

    OutputStosGroupNode = nornir_buildmanager.volumemanager.stosgroupnode.StosGroupNode(
        attrib=InputGroupNode.attrib.copy())
    (added, OutputStosGroupNode) = block_node.UpdateOrAddChildByAttrib(OutputStosGroupNode, 'Path')

    # Look at all of the mappings and create a list of potential control sections for each mapped section
    # Mappings = list(InputStosMapNode.findall('Mapping'))

    MappedToControlCandidateList = InputStosMapNode.MappedToControls()

    #    for mappingNode in Mappings:
    #        for mappedSection in mappingNode.Mapped:
    #            if mappedSection in MappedToControlCandidateList:
    #                MappedToControlCandidateList[mappedSection].append(mappingNode.Control)
    #            else:
    #                MappedToControlCandidateList[mappedSection] = [mappingNode.Control]

    # OK, fetch the mapped section numbers and lets work through them in order
    mappedSectionNumbers = list(MappedToControlCandidateList.keys())
    mappedSectionNumbers.sort()

    # If a section is used as a control, then prefer it when generat
    for mappedSection in mappedSectionNumbers:

        potentialControls = MappedToControlCandidateList[mappedSection]
        if len(potentialControls) == 0:
            # No need to test, copy over the transform
            Logger.error(str(mappedSection) + " -> ? No control section candidates found")
            continue

        knownControlSections = set(OutputStosMapNode.FindAllControlsForMapped(mappedSection))
        if knownControlSections == potentialControls:
            Logger.info(str(mappedSection) + " -> " + str(knownControlSections) + " was previously mapped, skipping")
            continue
        else:
            excess_control_sections = knownControlSections - potentialControls
            for control in excess_control_sections:
                OutputStosMapNode.RemoveMapping(control=control, mapped=mappedSection)
                Logger.info(f'Removing {mappedSection} -> {control}: No longer considered a valid control section.')
                prettyoutput.Log(
                    f'Removing {mappedSection} -> {control}: No longer considered a valid control section.')

        # Examine each stos image if it exists and determine the best match
        WinningTransform = None

        InputSectionMappingNode = InputGroupNode.GetSectionMapping(mappedSection)
        if InputSectionMappingNode is None:
            Logger.error(str(mappedSection) + " -> ? No SectionMapping data found")
            continue

        PotentialTransforms = []
        for controlSection in potentialControls:
            t = (controlSection, list(InputSectionMappingNode.TransformsToSection(controlSection)))
            PotentialTransforms.append(t)

        # Check if there is only one candidate
        if len(PotentialTransforms) == 1 and len(PotentialTransforms[0][1]) == 1:
            # No need to test, copy over the transform
            WinningTransform = PotentialTransforms[0][1][0]
        else:
            TaskList = []
            for (controlSection, Transforms) in PotentialTransforms:
                for Transform in Transforms:
                    try:
                        ImageSearchXPath = ImageSearchXPathTemplate % {'InputTransformChecksum': Transform.Checksum}
                        image_node = InputSectionMappingNode.find(ImageSearchXPath)

                        if image_node is None:
                            Logger.error(f'{mappedSection} -> {controlSection}')
                            Logger.error("No image node found for transform")
                            Logger.error("Checksum: " + Transform.Checksum)
                            continue

                        identifyCmd = 'magick identify -format %[mean] -verbose ' + image_node.FullPath

                        if Pool is None:
                            Pool = nornir_pools.GetLocalMachinePool()

                        task = Pool.add_process(image_node.attrib['Path'], identifyCmd + " && exit", shell=True)
                        task.transform_node = Transform
                        TaskList.append(task)
                        Logger.info("Evaluating " + str(mappedSection) + ' -> ' + str(controlSection))

                    except Exception as e:
                        Logger.error("Could not evalutate mapping " + str(mappedSection) + ' -> ' + str(controlSection))

            BestMean = None

            for t in TaskList:
                try:
                    MeanStr = t.wait_return()
                    MeanVal = float(MeanStr)
                    if BestMean is None:
                        WinningTransform = t.transform_node
                        BestMean = MeanVal
                    elif BestMean > float(MeanVal):
                        WinningTransform = t.transform_node
                        BestMean = MeanVal
                except:
                    pass

        if WinningTransform is None:
            Logger.error("Winning transform is none, section #" + str(mappedSection))
            Logger.info("No mapping found for " + str(mappedSection))
            continue

        OutputStosMapNode.AddMapping(WinningTransform.ControlSectionNumber, mappedSection)

        OutputSectionMappingNode = nornir_buildmanager.volumemanager.sectionmappingsnode.SectionMappingsNode.Create(
            attrib=InputSectionMappingNode.attrib)
        (added, OutputSectionMappingNode) = OutputStosGroupNode.UpdateOrAddChildByAttrib(OutputSectionMappingNode,
                                                                                         'MappedSectionNumber')

        (added, OutputTransformNode) = OutputSectionMappingNode.UpdateOrAddChildByAttrib(WinningTransform, 'Path')
        # OutputTransformNode.attrib = copy.deepcopy(WinningTransform.attrib)

        # if controlSection is None:
        #    Logger.info("No mapping found for " + str(mappedSection))
        # else:
        Logger.info("Created mapping " + str(mappedSection) + ' -> ' + str(WinningTransform.ControlSectionNumber))

    yield block_node


def __GetOrCreateInputStosFileForRegistration(stos_group_node: StosGroupNode, InputTransformNode: TransformNode,
                                              OutputDownsample: int, ControlFilter: FilterNode,
                                              MappedFilter: FilterNode, UseMasks: bool):
    '''
    :return: If a manual override stos file exists we return the manual file.  If it does not exist we scale the input transform to the desired size
    '''
    # Begin selecting the input transform for registration
    AutomaticInputDir = os.path.join(stos_group_node.FullPath, 'Automatic')
    os.makedirs(AutomaticInputDir, exist_ok=True)

    ExpectedStosFileName = nornir_buildmanager.volumemanager.stosgroupnode.StosGroupNode.GenerateStosFilename(
        ControlFilter, MappedFilter)

    # Copy the input stos or converted stos to the input directory
    AutomaticInputStosFullPath = os.path.join(AutomaticInputDir, InputTransformNode.Path)
    ManualInputStosFullPath = stos_group_node.PathToManualTransform(ExpectedStosFileName)

    InputStosFullPath = __SelectAutomaticOrManualStosFilePath(AutomaticInputStosFullPath=AutomaticInputStosFullPath,
                                                              ManualInputStosFullPath=ManualInputStosFullPath)
    InputChecksum = None
    if InputStosFullPath == AutomaticInputStosFullPath:
        __GenerateStosFileIfOutdated(InputTransformNode, AutomaticInputStosFullPath, OutputDownsample, ControlFilter,
                                     MappedFilter, UseMasks)
        InputChecksum = InputTransformNode.Checksum
    else:
        InputChecksum = stosfile.StosFile.LoadChecksum(InputStosFullPath)

    return InputStosFullPath, InputChecksum


def SetStosFileMasks(stosFullPath: str, ControlFilter: FilterNode, MappedFilter: FilterNode, UseMasks: bool,
                     Downsample: int):
    '''
    Ensure the stos file has masks
    '''

    OutputStos = stosfile.StosFile.Load(stosFullPath)
    if OutputStos.HasMasks == UseMasks:
        return
    else:
        if not UseMasks:
            if OutputStos.HasMasks:
                OutputStos.ClearMasks()
                OutputStos.Save(stosFullPath, AddMasks=False)

            return
        else:
            ControlMaskImageFullPath = ControlFilter.MaskImageset.GetOrPredictImageFullPath(Downsample)
            MappedMaskImageFullPath = MappedFilter.MaskImageset.GetOrPredictImageFullPath(Downsample)

            OutputStos.ControlMaskFullPath = ControlMaskImageFullPath
            OutputStos.MappedMaskFullPath = MappedMaskImageFullPath
            OutputStos.Save(stosFullPath)
            return


def IsStosNodeOutdated(InputTransformNode: TransformNode, OutputTransformNode: TransformNode, ControlFilter: FilterNode,
                       MappedFilter: FilterNode, UseMasks: bool,
                       OutputDownsample: int):
    '''
    :param InputTransformNode:
    :param OutputTransformNode:
    :param ControlFilter:
    :param MappedFilter:
    :param OutputDownsample:
    :param bool UseMasks: True if masks should be included.  None if we should use masks if they exist in the input stos transform
    :Return: true if the output stos transform is stale
    '''

    if not os.path.exists(OutputTransformNode.FullPath):
        return True

    if OutputTransformNode is None:
        return True

    OutputStosGroup = OutputTransformNode.GetParent('StosGroup')

    InputStos = stosfile.StosFile.Load(InputTransformNode.FullPath)
    if UseMasks is None:
        UseMasks = InputStos.HasMasks

    if 'InputTransformChecksum' in OutputTransformNode.attrib:
        if not transforms.IsValueMatched(OutputTransformNode, 'InputTransformChecksum', InputTransformNode.Checksum):
            return True

    if OutputStosGroup.AreStosInputImagesOutdated(OutputTransformNode, ControlFilter, MappedFilter,
                                                  MasksRequired=UseMasks):
        return True

    if UseMasks is None:
        InputStos = stosfile.StosFile.Load(InputTransformNode.FullPath)
        UseMasks = InputStos.HasMasks

    OutputStos = stosfile.StosFile.Load(OutputTransformNode.FullPath)
    if OutputStos.HasMasks != UseMasks:
        return True

    ControlImageFullPath = ControlFilter.Imageset.GetOrPredictImageFullPath(OutputDownsample)
    MappedImageFullPath = MappedFilter.Imageset.GetOrPredictImageFullPath(OutputDownsample)

    ControlMaskImageFullPath = None
    MappedMaskImageFullPath = None
    if UseMasks:
        ControlMaskImageFullPath = ControlFilter.MaskImageset.GetOrPredictImageFullPath(OutputDownsample)
        MappedMaskImageFullPath = MappedFilter.MaskImageset.GetOrPredictImageFullPath(OutputDownsample)

    return not (OutputStos.ControlImagePath == ControlImageFullPath and
                OutputStos.MappedImagePath == MappedImageFullPath and
                OutputStos.ControlMaskFullPath == ControlMaskImageFullPath and
                OutputStos.MappedMaskFullPath == MappedMaskImageFullPath and
                OutputStos.HasMasks == UseMasks)


def IsStosFileOutdated(InputTransformNode: TransformNode, OutputTransformPath: str, OutputDownsample: int,
                       ControlFilter: FilterNode, MappedFilter: FilterNode,
                       UseMasks: bool | None):
    '''
    :param InputTransformNode:
    :param OutputTransformPath:
    :param OutputDownsample:
    :param ControlFilter:
    :param MappedFilter:
    :param bool UseMasks: True if masks should be included.  None if we should use masks if they exist in the input stos transform
    :return: True if any part of the stos file is out of date compared to the input stos file
    '''

    # Replace the automatic files if they are outdated.
    result = files.RemoveOutdatedFile(InputTransformNode.FullPath, OutputTransformPath)

    if result is None:
        # if not os.path.exists(OutputTransformPath):
        return True

    if UseMasks is None:
        InputStos = stosfile.StosFile.Load(InputTransformNode.FullPath)
        UseMasks = InputStos.HasMasks

    OutputStos = stosfile.StosFile.Load(InputTransformNode.FullPath)
    if not OutputStos.HasMasks == UseMasks:
        return True

    ControlImageFullPath = ControlFilter.Imageset.GetOrPredictImageFullPath(OutputDownsample)
    MappedImageFullPath = MappedFilter.Imageset.GetOrPredictImageFullPath(OutputDownsample)

    if not (OutputStos.ControlImagePath == ControlImageFullPath and OutputStos.MappedImagePath == MappedImageFullPath):
        return True

    ControlMaskImageFullPath = None
    MappedMaskImageFullPath = None
    if UseMasks:
        ControlMaskImageFullPath = ControlFilter.MaskImageset.GetOrPredictImageFullPath(OutputDownsample)
        MappedMaskImageFullPath = MappedFilter.MaskImageset.GetOrPredictImageFullPath(OutputDownsample)

        if not OutputStos.ControlMaskFullPath == ControlMaskImageFullPath and OutputStos.MappedMaskFullPath == MappedMaskImageFullPath:
            return True

    return False


def __GenerateStosFileIfOutdated(InputTransformNode: TransformNode, OutputTransformPath: str, OutputDownsample: int,
                                 ControlFilter: FilterNode, MappedFilter: FilterNode,
                                 UseMasks: bool):
    '''Only generates a stos file if the Output stos path has an earlier last modified time compared to the input
    :param bool UseMasks: True if masks should be included.  None if we should copy setting from input stos transform
    :return: True if a file was generated and saved, False if the output already existed and was valid
    '''

    # We should not be trying to create output if we have no input
    # assert(os.path.exists(InputTransformNode.FullPath))
    if IsStosFileOutdated(InputTransformNode, OutputTransformPath, OutputDownsample, ControlFilter, MappedFilter,
                          UseMasks):
        try:
            os.remove(OutputTransformPath)
        except (OSError, FileNotFoundError):
            pass

    if not os.path.exists(OutputTransformPath):
        result = __GenerateStosFile(InputTransformNode, OutputTransformPath, OutputDownsample, ControlFilter,
                                    MappedFilter,
                                    UseMasks)
        result.Save(OutputTransformPath)
        return True

    return False


def __PredictStosImagePaths(filter_node: FilterNode, Downsample: int):
    '''
    .stos files embed file names.  However if we scale .stos files past the point where they have an assembled image we need to put the filename we would expect 
    without actually creating meta-data for a non-existent image level 
    '''

    imageFullPath = filter_node.PredictImageFullPath(Downsample)
    maskFullPath = filter_node.PredictMaskFullPath(Downsample) if filter_node.HasMask else None
    return imageFullPath, maskFullPath


def __GenerateStosFile(InputTransformNode: TransformNode, OutputTransformPath: str, OutputDownsample: int,
                       ControlFilter: FilterNode, MappedFilter: FilterNode,
                       UseMasks: bool | None):
    '''Generates a new stos file using the specified filters and scales the transform to match the
       requested downsample as needed.
       :param UseMasks: True if masks should be included.  None if we should copy setting from input stos transform
       :rtype: bool
       :return: A StosFile if a new file was needed, otherwise None
    '''

    stos_group_node = InputTransformNode.FindParent('StosGroup')
    InputDownsample = stos_group_node.Downsample

    InputStos = stosfile.StosFile.Load(InputTransformNode.FullPath)
    if UseMasks is None:
        UseMasks = InputStos.HasMasks

    ControlImageFullPath = ControlFilter.Imageset.GetOrPredictImageFullPath(OutputDownsample)
    MappedImageFullPath = MappedFilter.Imageset.GetOrPredictImageFullPath(OutputDownsample)

    ControlMaskImageFullPath = None
    MappedMaskImageFullPath = None
    if UseMasks:
        ControlMaskImageFullPath = ControlFilter.MaskImageset.GetOrPredictImageFullPath(OutputDownsample)
        MappedMaskImageFullPath = MappedFilter.MaskImageset.GetOrPredictImageFullPath(OutputDownsample)

    # If all the core details are the same we can save time by copying the data instead
    if not (InputStos.ControlImagePath == ControlImageFullPath and
            InputStos.MappedImagePath == MappedImageFullPath and
            InputStos.ControlMaskFullPath == ControlMaskImageFullPath and
            InputStos.MappedMaskFullPath == MappedMaskImageFullPath and
            OutputDownsample == InputDownsample and
            InputStos.HasMasks == UseMasks):

        ModifiedInputStos = InputStos.ChangeStosGridPixelSpacing(oldspacing=InputDownsample,
                                                                 newspacing=OutputDownsample,
                                                                 ControlImageFullPath=ControlImageFullPath,
                                                                 MappedImageFullPath=MappedImageFullPath,
                                                                 ControlMaskFullPath=ControlMaskImageFullPath,
                                                                 MappedMaskFullPath=MappedMaskImageFullPath)

        return ModifiedInputStos
    else:
        return None


def __SelectAutomaticOrManualStosFilePath(AutomaticInputStosFullPath: str, ManualInputStosFullPath: str):
    ''' Use the manual stos file if it exists, prevent any cleanup from occurring on the manual file '''

    # If we know there is no manual file, then use the automatic file
    if not ManualInputStosFullPath:
        return AutomaticInputStosFullPath

    # If we haven't generated an automatic file and a manual file exists, use the manual file.  Delete the automatic if it also exists.
    # InputStosFullPath = AutomaticInputStosFullPath
    if os.path.exists(ManualInputStosFullPath):
        # InputStosFullPath = ManualInputStosFullPath
        # Files.RemoveOutdatedFile(ManualInputStosFullPath, OutputStosFullPath)

        try:

            # Clean up the automatic input if we have a manual override
            os.remove(AutomaticInputStosFullPath)
        except (OSError, FileNotFoundError):
            pass

        return ManualInputStosFullPath
    # else:
    # The local copy may have a different downsample level, in which case the checksums based on the transform would always be different
    # As a result we need to use the meta-data checksum records and not the automatically generated file.
    # In this case we should delete the automatic file and let it regenerate to be sure it is always fresh when the script executes
    # if os.path.exists(AutomaticInputStosFullPath):
    # os.remove(AutomaticInputStosFullPath)

    return AutomaticInputStosFullPath


def __RunIrStosGridCmd(InputStosFullPath: str, OutputStosFullPath: str, **kwargs):
    '''Run SCI's original stos refinement algorithm'''
    argstring = misc.ArgumentsFromDict(kwargs)
    StosGridTemplate = 'ir-stos-grid -save %(OutputStosFullPath)s -load %(InputStosFullPath)s ' + argstring

    cmd = StosGridTemplate % {'OutputStosFullPath': OutputStosFullPath,
                              'InputStosFullPath': InputStosFullPath}

    prettyoutput.Log(cmd)
    subprocess.call(cmd + " && exit", shell=True)


def __RunPythonGridRefinementCmd(InputStosFullPath: str, OutputStosFullPath: str, **kwargs):
    '''Run the native python refinement algorithm'''

    if 'SaveImages' in kwargs or 'SavePlots' in kwargs:
        # We need to specify an output directory if we are saving plots
        kwargs['outputDir'] = os.path.dirname(OutputStosFullPath)

    prettyoutput.Log(f'Refining {InputStosFullPath} to {OutputStosFullPath}')

    try:
        local_distortion_correction.RefineStosFile(InputStos=InputStosFullPath,
                                                   OutputStosPath=OutputStosFullPath,
                                                   **kwargs)
    except ValueError as e:
        prettyoutput.LogErr(f'Refining {InputStosFullPath} to {OutputStosFullPath} Failed!')
        return

    prettyoutput.Log(f'Refining {InputStosFullPath} to {OutputStosFullPath} Complete!')


def IrStosGridRefine(Parameters, mapping_node: MappingNode, InputGroupNode: StosGroupNode, UseMasks: bool, Downsample: int = 32,
                     ControlFilterPattern: str | None = None, MappedFilterPattern: str | None = None,
                     OutputStosGroup=None,
                     Type=None):
    '''
    Invoke a command to execute a function with an input and output .stos file.  The function
    is expected to refine the input .stos file and write the output .stos file.  At the time
    this function was written there was a ir-refine-grid command line program and a native
    python implementation.
    '''

    return RefineInvoker(__RunIrStosGridCmd,
                         Parameters=Parameters,
                         mapping_node=mapping_node,
                         InputGroupNode=InputGroupNode,
                         UseMasks=UseMasks,
                         Downsample=Downsample,
                         ControlFilterPattern=ControlFilterPattern,
                         MappedFilterPattern=MappedFilterPattern,
                         OutputStosGroup=OutputStosGroup,
                         Type=Type
                         )


def StosGridRefine(Parameters, mapping_node: MappingNode, InputGroupNode, IgnoreMasks, Downsample=32,
                   ControlFilterPattern=None, MappedFilterPattern=None, OutputStosGroup=None,
                   Type=None, MappedSections: None | list[int] = None, **kwargs) -> Generator[
    XElementWrapper, None, None]:
    '''
    Invoke a command to execute a function with an input and output .stos file.  The function
    is expected to refine the input .stos file and write the output .stos file.  At the time
    this function was written there was a ir-refine-grid command line program and a native
    python implementation.
    '''

    # strip any XElements from kwargs before passing them on
    keys_to_strip = []
    for k, v in kwargs.items():
        if isinstance(v, nornir_buildmanager.volumemanager.XElementWrapper):
            keys_to_strip.append(k)

    for k in keys_to_strip:
        del kwargs[k]

    if MappedSections is not None:
        MappedSections = frozenset(MappedSections)

    return RefineInvoker(__RunPythonGridRefinementCmd,
                         mapping_node=mapping_node,
                         InputGroupNode=InputGroupNode,
                         UseMasks=not IgnoreMasks,
                         Downsample=Downsample,
                         ControlFilterPattern=ControlFilterPattern,
                         MappedFilterPattern=MappedFilterPattern,
                         OutputStosGroup=OutputStosGroup,
                         Type=Type, MappedSections=MappedSections, **kwargs)


def RefineInvoker(RefineFunc, mapping_node: MappingNode, InputGroupNode: StosGroupNode,
                  UseMasks: bool, Downsample: int = 32,
                  ControlFilterPattern: str | None = None, MappedFilterPattern: str | None = None,
                  OutputStosGroup: str | None = None, Type: str | None = None,
                  MappedSections: None | frozenset[int] = None,
                  **kwargs) -> Generator[XElementWrapper, None, None]:
    '''
    :param mapping_node:
    :param InputGroupNode:
    :param UseMasks:
    :param Downsample:
    :param ControlFilterPattern:
    :param MappedFilterPattern:
    :param OutputStosGroup:
    :param Type:
    :param func RefineFunc: Function to invoke when we have identified a stos file needing refinement
    '''

    Logger = logging.getLogger(__name__ + '.StosGrid')

    block_node = InputGroupNode.FindParent('Block')

    if OutputStosGroup is None:
        OutputStosGroup = 'Grid'

    OutputStosGroupName = OutputStosGroup

    if Type is None:
        Type = 'Grid'

    (added, OutputStosGroupNode) = block_node.GetOrCreateStosGroup(OutputStosGroupName, Downsample)
    OutputStosGroupNode.CreateDirectories()

    if added:
        yield block_node

    for MappedSection in mapping_node.Mapped:

        if MappedSections is not None and MappedSection not in MappedSections:
            continue

        # Find the inputTransformNode in the InputGroupNode
        InputTransformNodes = list(InputGroupNode.TransformsForMapping(MappedSection, mapping_node.Control))
        if InputTransformNodes is None or len(InputTransformNodes) == 0:
            Logger.warning("No transform found for mapping " + str(MappedSection) + " -> " + str(mapping_node.Control))
            continue

        for InputTransformNode in InputTransformNodes:
            OutputDownsample = Downsample

            InputSectionMappingNode = InputTransformNode.FindParent('SectionMappings')
            OutputSectionMappingNode = nornir_buildmanager.volumemanager.sectionmappingsnode.SectionMappingsNode.Create(
                **InputSectionMappingNode.attrib)
            

            ControlFilter = __GetFirstMatchingFilter(block_node,
                                                     InputTransformNode.ControlSectionNumber,
                                                     InputTransformNode.ControlChannelName,
                                                     ControlFilterPattern)

            MappedFilter = __GetFirstMatchingFilter(block_node,
                                                    InputTransformNode.MappedSectionNumber,
                                                    InputTransformNode.MappedChannelName,
                                                    MappedFilterPattern)
            
            (added, OutputSectionMappingNode) = OutputStosGroupNode.UpdateOrAddChildByAttrib(OutputSectionMappingNode,
                                                                                          'MappedSectionNumber')
            
            existing_output_transform_node = None
            if added:
                yield OutputStosGroupNode
            else:
                existing_output_transform_node = OutputSectionMappingNode.FindStosTransform(ControlSectionNumber=mapping_node.Control,
                                                                                            ControlChannelName=InputTransformNode.ControlChannelName,
                                                                                            ControlFilterName=InputTransformNode.ControlFilterName,
                                                                                            MappedSectionNumber=MappedSection,
                                                                                            MappedChannelName=InputTransformNode.MappedChannelName,
                                                                                            MappedFilterName=InputTransformNode.MappedFilterName) 

            if ControlFilter is None:
                Logger.warning("No control filter, skipping refinement")
                if existing_output_transform_node is not None:
                    existing_output_transform_node.Clean("No control filter found in stos grid") 

            if MappedFilter is None:
                Logger.warning("No mapped filter, skipping refinement")
                if existing_output_transform_node is not None:
                    existing_output_transform_node.Clean("No mapped filter found in stos grid")
                     
            if ControlFilter is None or MappedFilter is None:
                yield OutputStosGroupNode
                continue

            try:
                # Ensure the input images exist on disk and generate them if not
                GetOrCreateRegistrationImageNodes(ControlFilter, OutputDownsample, GetMask=UseMasks, Logger=Logger)
                GetOrCreateRegistrationImageNodes(MappedFilter, OutputDownsample, GetMask=UseMasks, Logger=Logger)
            except NornirUserException as e:
                # This exception is raised if the input images cannot be generated
                prettyoutput.LogErr(str(e))
                continue

            OutputFile = nornir_buildmanager.volumemanager.stosgroupnode.StosGroupNode.GenerateStosFilename(
                ControlFilter, MappedFilter)
            OutputStosFullPath = os.path.join(OutputStosGroupNode.FullPath, OutputFile)
            stosNode = existing_output_transform_node if existing_output_transform_node is not None else OutputStosGroupNode.GetStosTransformNode(ControlFilter, MappedFilter)
            if stosNode is None:
                stosNode = OutputStosGroupNode.CreateStosTransformNode(ControlFilter, MappedFilter, OutputType=Type,
                                                                       OutputPath=OutputFile)

            (InputStosFullPath, InputStosFileChecksum) = __GetOrCreateInputStosFileForRegistration(
                stos_group_node=OutputStosGroupNode,
                InputTransformNode=InputTransformNode,
                ControlFilter=ControlFilter,
                MappedFilter=MappedFilter,
                OutputDownsample=OutputDownsample,
                UseMasks=UseMasks)

            if not os.path.exists(InputStosFullPath):
                # Hmm... no input.  This is worth reporting and moving on
                Logger.error("ir-stos-grid did not produce output for " + InputStosFullPath)
                InputGroupNode.remove(InputTransformNode)
                continue

            # If the manual or automatic stos file is newer than the output, remove the output
            # files.RemoveOutdatedFile(InputTransformNode.FullPath, OutputStosFullPath): 

            # Remove our output if it was generated from an input transform with a different checksum
            if os.path.exists(OutputStosFullPath):
                # stosNode = OutputSectionMappingNode.GetChildByAttrib('Transform', 'ControlSectionNumber', InputTransformNode.ControlSectionNumber)
                if stosNode is not None:
                    if OutputStosGroupNode.AreStosInputImagesOutdated(stosNode, ControlFilter, MappedFilter,
                                                                      MaskRequired=UseMasks):
                        stosNode.Clean("Input images outdated for %s" % stosNode.FullPath)
                        stosNode = None
                    elif 'InputTransformChecksum' in stosNode.attrib:
                        stosNode = transforms.RemoveOnMismatch(stosNode, 'InputTransformChecksum',
                                                               InputStosFileChecksum)
                        # if(InputStosFileChecksum != stosNode.InputTransformChecksum):
                        # os.remove(OutputStosFullPath)

                        # Remove old stos meta-data and create from scratch to avoid stale data.
                        # OutputSectionMappingNode.remove(stosNode)
                    elif 'InputTransformChecksum' not in stosNode.attrib:
                        stosNode.Clean(
                            "InputTransformChecksum attribute is required on transform element and was not found")
                        stosNode = None
                        # ## Uncomment to preserve the existing file and simply update the meta-data
                        # stosNode.ResetChecksum()
                        # stosNode.SetTransform(InputTransformNode)
                        # stosNode.InputTransformChecksum = InputStosFileChecksum
                        # yield OutputSectionMappingNode
                        # return

                if stosNode is None:
                    stosNode = OutputStosGroupNode.CreateStosTransformNode(ControlFilter, MappedFilter, OutputType=Type,
                                                                           OutputPath=OutputFile)

            #                    else:
            #                        os.remove(OutputStosFullPath)

            # Replace the automatic files if they are outdated.
            # GenerateStosFile(InputTransformNode, AutomaticInputStosFullPath, OutputDownsample, ControlFilter, MappedFilter)

            #        FixStosFilePaths(ControlFilter, MappedFilter, InputTransformNode, OutputDownsample, StosFilePath=InputStosFullPath)
            if not os.path.exists(OutputStosFullPath):
                ManualStosFileFullPath = OutputStosGroupNode.PathToManualTransform(stosNode.FullPath)
                if ManualStosFileFullPath is None:

                    try:
                        RefineFunc(InputStosFullPath, OutputStosFullPath, **kwargs)
                    except Exception as e:
                        prettyoutput.Log(f"Exception calling stos refine function {RefineFunc}:\n{e}\n\n")
                        raise

                    if not os.path.exists(OutputStosFullPath):
                        Logger.error("ir-stos-grid did not produce output for " + InputStosFullPath)
                        OutputSectionMappingNode.remove(stosNode)
                        stosNode = None
                        yield OutputSectionMappingNode
                        continue
                    else:
                        if not stosfile.StosFile.IsValid(OutputStosFullPath):
                            os.remove(OutputStosFullPath)
                            OutputSectionMappingNode.remove(stosNode)
                            stosNode = None
                            prettyoutput.Log(
                                "Transform generated by refine was unable to be loaded. Deleting.  Check input transform: " + OutputStosFullPath)
                            yield OutputSectionMappingNode
                            continue
                else:
                    prettyoutput.Log(
                        "Copy manual override stos file to output: " + os.path.basename(ManualStosFileFullPath))
                    shutil.copy(ManualStosFileFullPath, OutputStosFullPath)

                    # Ensure we add or remove masks according to the parameters
                    SetStosFileMasks(OutputStosFullPath, ControlFilter, MappedFilter, UseMasks,
                                     OutputStosGroupNode.Downsample)

                stosNode.Path = OutputFile

                if os.path.exists(OutputStosFullPath):
                    stosNode.ResetChecksum()
                    stosNode.SetTransform(InputTransformNode)
                    stosNode.InputTransformChecksum = InputStosFileChecksum

                yield OutputSectionMappingNode


def __StosMapToRegistrationTree(stos_map_node: StosMapNode):
    '''Convert a collection of stos mappings into a tree.  The tree describes which transforms must be used to map points between sections'''

    rt = registrationtree.RegistrationTree()

    for mappingNode in stos_map_node.Mappings:
        for mappedSection in mappingNode.Mapped:
            rt.AddPair(mappingNode.Control, mappedSection)

    return rt


def __RegistrationTreeToSliceToVolumeMap(rt: registrationtree.RegistrationTree, StosMapName: str):
    '''Create a stos map where every mapping transforms to the root of the tree'''

    OutputStosMap = nornir_buildmanager.volumemanager.stosmapnode.StosMapNode.Create(StosMapName)

    for step in rt.GenerateOrderedMappingsToRoots():
        rootNode = step.RootNode
        mappedNode = step.MappedNode
        print("Mapping {0} -> {1}".format(mappedNode.SectionNumber, rootNode.SectionNumber))
        OutputStosMap.AddMapping(rootNode.SectionNumber, mappedNode.SectionNumber)

    return OutputStosMap


#
# def __AddRegistrationTreeNodeToStosMap(StosMapNode, rt, controlSectionNumber, mappedSectionNumber=None):
#     '''
#     Adds registration tree nodes to the stos map
#     
#     :param registrationtree.RegistrationTree rt: Registration Tree
#     :param int controlSectionNumber: Control Section Number
#     :param int mappedSectionNumber: Either an int or a RegistrationTreeNode for the Mapped Section Number
#     '''
#     
#     if mappedSectionNumber is None:
#         mappedSectionNumber = controlSectionNumber
#      
#     nodeStack = [mappedSectionNumber]
#     alreadyMapped = set()
#         
#     while len(nodeStack) > 0:
#         
#         rtNode = None #Registration tree node
#         mappedSectionNumber = nodeStack.pop()
#         
#         if isinstance(mappedSectionNumber, registrationtree.RegistrationTreeNode):
#             rtNode = mappedSectionNumber
#             mappedSectionNumber = mappedSectionNumber.SectionNumber
#         elif mappedSectionNumber in rt.Nodes:
#             rtNode = rt.Nodes[mappedSectionNumber]
#         else:
#             raise ValueError("Unexpected mappedSectionNumber {0}".format(mappedSectionNumber))
#             continue #Not sure how we could reach this state
#                
#         alreadyMapped.union([mappedSectionNumber])
#         print("Mapping {0} -> {1}".format(str(mappedSectionNumber), controlSectionNumber))
#        
#         # Can loop forever here if a section is mapped twice
#         for mapped in rtNode.Children:
#             StosMapNode.AddMapping(controlSectionNumber, mapped.SectionNumber)
#             
#             if mapped.SectionNumber in rt.Nodes and mapped.SectionNumber not in alreadyMapped:
#                 nodeStack.append(mapped.SectionNumber)
#                 


def __AddRegistrationTreeNodeToStosMapRecursive(stos_map_node: StosMapNode, rt: registrationtree.RegistrationTree,
                                                controlSectionNumber: int, mappedSectionNumber: int | None = None):
    '''recursively adds registration tree nodes to the stos map
    
    This function was exceeding the recursion limit so it was replaced
    '''

    if mappedSectionNumber is None:
        mappedSectionNumber = controlSectionNumber
    elif isinstance(mappedSectionNumber, registrationtree.RegistrationTreeNode):
        mappedSectionNumber = mappedSectionNumber.SectionNumber

    print("Adding " + str(mappedSectionNumber))

    rtNode = None
    if mappedSectionNumber in rt.Nodes:
        rtNode = rt.Nodes[mappedSectionNumber]
    else:
        return

    # Can loop forever here if a section is mapped twice*/
    for mapped in rtNode.Children:
        stos_map_node.AddMapping(controlSectionNumber, mapped.SectionNumber)

        if mapped.SectionNumber in rt.Nodes:
            __AddRegistrationTreeNodeToStosMapRecursive(stos_map_node, rt, controlSectionNumber, mapped.SectionNumber)


def TranslateVolumeToZeroOrigin(stos_group_node: StosGroupNode, **kwargs):
    vol = stosgroupvolume.StosGroupVolume.Load(stos_group_node)

    vol.TranslateToZeroOrigin()

    SavedStosGroupNode = vol.Save()

    return SavedStosGroupNode


def BuildSliceToVolumeTransforms(stos_map_node: nornir_buildmanager.volumemanager.StosMapNode,
                                 stos_group_node: nornir_buildmanager.volumemanager.StosGroupNode,
                                 OutputMap: str,
                                 OutputGroupName: str,
                                 Downsample, Enrich: bool,
                                 Tolerance: float | None,
                                 linear_blend_factor: float | None=None,
                                 travel_limit: float | None=None,  
                                 ignore_rotation: bool = False,
                                  **kwargs):
    '''Build a slice-to-volume transform for each section referenced in the StosMap

    :param stos_map_node:
    :param stos_group_node:
    :param OutputGroupName:
    :param Downsample:
    :param str OutputMap: Name of the StosMap to create, defaults to StosGroupNode name if None
    :param bool Enrich: True if additional control points should be added if the transformed centroids of delaunay triangles are too far from expected position
    :param float Tolerance: The maximum distance the transformed and actual centroids can be before an additional control point is added at the centroid
    '''

    block_node = stos_group_node.Parent
    InputStosGroupNode = stos_group_node

    if not OutputMap:
        OutputMap = OutputGroupName

    OutputGroupFullname = '%s%d' % (OutputGroupName, Downsample)

    if not Enrich:
        Tolerance = None
    else:
        # Scale the tolerance for the downsample level
        Tolerance /= float(Downsample)

    rt = __StosMapToRegistrationTree(stos_map_node)

    if len(rt.RootNodes) == 0:
        return

    (AddedGroupNode, OutputGroupNode) = block_node.GetOrCreateStosGroup(OutputGroupFullname,
                                                                        InputStosGroupNode.Downsample)
    if AddedGroupNode:
        (yield block_node)

    # build the stos map again if it exists
    block_node.RemoveStosMap(map_name=OutputMap)

    OutputStosMap = __RegistrationTreeToSliceToVolumeMap(rt, OutputMap)
    (AddedStosMap, OutputStosMap) = block_node.UpdateOrAddChildByAttrib(OutputStosMap)
    OutputStosMap.CenterSection = stos_map_node.CenterSection

    if AddedStosMap:
        (yield block_node)

    for sectionNumber in rt.RootNodes:
        Node = rt.Nodes[sectionNumber]
        yield from SliceToVolumeFromRegistrationTreeNode(rt, Node, InputGroupNode=InputStosGroupNode,
                                                         OutputGroupNode=OutputGroupNode, EnrichTolerance=Tolerance,
                                                         ControlToVolumeTransform=None,
                                                         linear_blend_factor=linear_blend_factor,
                                                         travel_limit=travel_limit,  
                                                         ignore_rotation=ignore_rotation)
        # for saveNode in SliceToVolumeFromRegistrationTreeNode(rt, Node, InputGroupNode=InputStosGroupNode, OutputGroupNode=OutputGroupNode, EnrichTolerance=Tolerance, ControlToVolumeTransform=None):
        #    (yield saveNode)

    # TranslateVolumeToZeroOrigin(OutputGroupNode)
    # Do not use TranslateVolumeToZeroOrigin here because the center of the volume image does not get shifted with the rest of the sections. That is a problem.  We should probably create an identity transform for the root nodes in
    # the registration tree


def SliceToVolumeFromRegistrationTreeNode(rt: registrationtree.RegistrationTree,
                                          rootNode: registrationtree.RegistrationTreeNode,
                                          InputGroupNode: StosGroupNode, OutputGroupNode: StosGroupNode,
                                          EnrichTolerance=float | None,
                                          ControlToVolumeTransform=None,
                                          linear_blend_factor: float | None=None,
                                          travel_limit: float | None=None,  
                                          ignore_rotation: bool = False):
    Logger = logging.getLogger(__name__ + '.SliceToVolumeFromRegistrationTreeNode')

    SectionToRootTransformMap = {}
    for step in rt.GenerateOrderedMappingsToRootNode(rootNode):
        MappedSectionNode = step.MappedNode
        IntermediateControlSection = step.ParentNode.SectionNumber

        mappedSectionNumber = MappedSectionNode.SectionNumber

        logStr = f"{mappedSectionNumber:d} -> {IntermediateControlSection:d} -> {rootNode.SectionNumber:d}"
        verboseStr = f"Mapping {mappedSectionNumber:d} -> {IntermediateControlSection:d} -> {rootNode.SectionNumber:d} to {mappedSectionNumber:d} -> {rootNode.SectionNumber:d}"
        # Logger.info(logStr)
        prettyoutput.Log(verboseStr)

        MappedToControlTransforms = list(
            InputGroupNode.TransformsForMapping(mappedSectionNumber, IntermediateControlSection))

        if MappedToControlTransforms is None or len(MappedToControlTransforms) == 0:
            errStr = f"{mappedSectionNumber} -> {IntermediateControlSection} mapping does not have a .stos transform at {InputGroupNode.FullPath}"
            prettyoutput.LogErr(errStr)
            continue

        # If we managed to locate an input .stos transform create the mapping
        (MappingAdded, OutputSectionMappingsNode) = OutputGroupNode.GetOrCreateSectionMapping(mappedSectionNumber)
        if MappingAdded:
            (yield OutputGroupNode)

        # In theory each iteration of this loop could be run in a seperate thread.  Useful when center is in center of volume.
        for MappedToControlTransform in MappedToControlTransforms:

            ControlSectionNumber = None
            ControlChannelName = None
            ControlFilterName = None

            ControlToVolumeTransform = None
            ControlToVolumeTransformKey = (IntermediateControlSection, rootNode.SectionNumber)
            if ControlToVolumeTransformKey in SectionToRootTransformMap:
                # prettyoutput.Log("Looking for {0}: FOUND".format(str(ControlToVolumeTransformKey)))
                ControlToVolumeTransform = SectionToRootTransformMap[ControlToVolumeTransformKey]
            elif ControlToVolumeTransformKey[0] != ControlToVolumeTransformKey[1]:
                # Do not bother printing an error if there is no intermediate transform to find
                errStr = f"Could not find {ControlToVolumeTransformKey[0]} -> {ControlToVolumeTransformKey[1]} .stos transform"
                prettyoutput.LogErr(errStr)

            if ControlToVolumeTransform is None:
                ControlSectionNumber = MappedToControlTransform.ControlSectionNumber
                ControlChannelName = MappedToControlTransform.ControlChannelName
                ControlFilterName = MappedToControlTransform.ControlFilterName
            else:
                ControlSectionNumber = ControlToVolumeTransform.ControlSectionNumber
                ControlChannelName = ControlToVolumeTransform.ControlChannelName
                ControlFilterName = ControlToVolumeTransform.ControlFilterName

            OutputTransform = OutputSectionMappingsNode.FindStosTransform(ControlSectionNumber=ControlSectionNumber,
                                                                          ControlChannelName=ControlChannelName,
                                                                          ControlFilterName=ControlFilterName,
                                                                          MappedSectionNumber=MappedToControlTransform.MappedSectionNumber,
                                                                          MappedChannelName=MappedToControlTransform.MappedChannelName,
                                                                          MappedFilterName=MappedToControlTransform.MappedFilterName)

            if OutputTransform is None:
                OutputTransform = nornir_buildmanager.volumemanager.TransformNode(
                    attrib=MappedToControlTransform.attrib)
                OutputTransform.Name = str(mappedSectionNumber) + '-' + str(IntermediateControlSection)
                OutputTransform.SetTransform(MappedToControlTransform)
                OutputTransformAdded = OutputSectionMappingsNode.AddOrUpdateTransform(OutputTransform)
                OutputTransform.Path = OutputTransform.Name + '.stos'  # Path creates directory and the fullpath parameter is missing.  Needs to run after the transform is added                                

                # Remove any residual transform file just in case
                if os.path.exists(OutputTransform.FullPath):
                    os.remove(OutputTransform.FullPath)

            if ControlToVolumeTransform is not None:
                OutputTransform.Path = str(mappedSectionNumber) + '-' + str(
                    ControlToVolumeTransform.ControlSectionNumber) + '.stos'

            if not OutputTransform.IsInputTransformMatched(MappedToControlTransform):
                Logger.info(" %s: Removed outdated transform %s" % (logStr, OutputTransform.Path))
                if os.path.exists(OutputTransform.FullPath):
                    os.remove(OutputTransform.FullPath)

            # ===================================================================
            # if not hasattr(OutputTransform, 'InputTransformChecksum'):
            #     if os.path.exists(OutputTransform.FullPath):
            #         os.remove(OutputTransform.FullPath)
            # else:
            #     if not MappedToControlTransform.Checksum == OutputTransform.InputTransformChecksum:
            #         if os.path.exists(OutputTransform.FullPath):
            #             os.remove(OutputTransform.FullPath)
            # ===================================================================

            # ControlToVolumeTransform can be none if:
            # 1) There was an error generating an earlier slice to volume transform,
            # 2) The .stos transform maps directly to the center without an intermediate.  In which case we need to skip all further steps 
            if ControlToVolumeTransform is None:

                # Handle the case of a transform that is mapped directly to the origin.  
                if ControlToVolumeTransformKey[0] == ControlToVolumeTransformKey[1]:
                    # This maps directly to the origin, add it to the output stos group
                    # Files.RemoveOutdatedFile(MappedToControlTransform.FullPath, OutputTransform.FullPath )

                    if not os.path.exists(OutputTransform.FullPath):
                        try:
                            Logger.info(
                                " %s: Copy mapped to volume center stos transform %s" % (logStr, OutputTransform.Path))
                            shutil.copy(MappedToControlTransform.FullPath, OutputTransform.FullPath)
                            OutputTransform.ResetChecksum()
                            # OutputTransform.Checksum = MappedToControlTransform.Checksum
                            OutputTransform.SetTransform(MappedToControlTransform)
                        except FileNotFoundError as e:
                            errorStr = " %s: Unable to copy mapped to volume center stos transform %s:\n%s" % (
                                logStr, OutputTransform.Path, str(e))
                            Logger.error(errorStr)
                            prettyoutput.LogErr(errorStr)
                            continue

                        (yield OutputSectionMappingsNode)
                else:
                    # If we can't generate a transform we continue.  This allows other mapping to the center of the volume to still generate
                    continue

            else:
                OutputTransform.ControlSectionNumber = ControlToVolumeTransform.ControlSectionNumber
                OutputTransform.ControlChannelName = ControlToVolumeTransform.ControlChannelName
                OutputTransform.ControlFilterName = ControlToVolumeTransform.ControlFilterName

                if hasattr(OutputTransform, "ControlToVolumeTransformChecksum"):
                    if not OutputTransform.ControlToVolumeTransformChecksum == ControlToVolumeTransform.Checksum:
                        Logger.info(" %s: ControlToVolumeTransformChecksum mismatch, removing" % logStr)
                        if os.path.exists(OutputTransform.FullPath):
                            os.remove(OutputTransform.FullPath)
                elif os.path.exists(OutputTransform.FullPath):
                    os.remove(OutputTransform.FullPath)

                if not os.path.exists(OutputTransform.FullPath):

                    try:
                        # Logger.info(" %s: Adding transforms" % (logStr))
                        prettyoutput.Log("\tCalculating new .stos")
                        MToVStos = stosfile.AddStosTransforms(MappedToControlTransform.FullPath,
                                                              ControlToVolumeTransform.FullPath,
                                                              EnrichTolerance=EnrichTolerance,
                                                              linear_factor=linear_blend_factor,
                                                              travel_limit=travel_limit,
                                                              ignore_rotation=ignore_rotation)
                        MToVStos.Save(OutputTransform.FullPath)

                        OutputTransform.ControlToVolumeTransformChecksum = ControlToVolumeTransform.Checksum
                        OutputTransform.ResetChecksum()
                        OutputTransform.SetTransform(MappedToControlTransform)
                        # OutputTransform.Checksum = stosfile.StosFile.LoadChecksum(OutputTransform.FullPath)

                    except ValueError as e:
                        # Probably an invalid transform.  Skip it
                        prettyoutput.LogErr(str(e))
                        Logger.error(str(e))
                        OutputTransform.Clean()
                        OutputTransform = None
                        continue

                    (yield OutputSectionMappingsNode)
                else:
                    Logger.info(" %s: is still valid" % logStr)

            newTransformKey = (OutputTransform.MappedSectionNumber, ControlToVolumeTransformKey[1])
            SectionToRootTransformMap[newTransformKey] = OutputTransform
            # print("Added key {0}".format(newTransformKey))


#                 for retval in SliceToVolumeFromRegistrationTreeNode(rt,
#                                                                     mappedNode,
#                                                                     InputGroupNode,
#                                                                     OutputGroupNode, 
#                                                                     EnrichTolerance=EnrichTolerance, 
#                                                                     ControlToVolumeTransform=OutputTransform):
#                     yield retval


def SliceToVolumeFromRegistrationTreeNodeRecursive(rt, Node, InputGroupNode, OutputGroupNode, EnrichTolerance,
                                                   ControlToVolumeTransform=None):
    ControlSection = Node.SectionNumber

    Logger = logging.getLogger(__name__ + '.SliceToVolumeFromRegistrationTreeNode')

    for MappedSectionNode in Node.Children:
        mappedSectionNumber = MappedSectionNode.SectionNumber
        mappedNode = rt.Nodes[mappedSectionNumber]

        logStr = "%s <- %s" % (str(ControlSection), str(mappedSectionNumber))

        (MappingAdded, OutputSectionMappingsNode) = OutputGroupNode.GetOrCreateSectionMapping(mappedSectionNumber)
        if MappingAdded:
            yield OutputGroupNode

        MappedToControlTransforms = InputGroupNode.TransformsForMapping(mappedSectionNumber, ControlSection)

        if MappedToControlTransforms is None or len(MappedToControlTransforms) == 0:
            Logger.error(" %s : No transform found:" % logStr)
            continue

        # In theory each iteration of this loop could be run in a seperate thread.  Useful when center is in center of volume.
        for MappedToControlTransform in MappedToControlTransforms:

            ControlSectionNumber = None
            ControlChannelName = None
            ControlFilterName = None

            if ControlToVolumeTransform is None:
                ControlSectionNumber = MappedToControlTransform.ControlSectionNumber
                ControlChannelName = MappedToControlTransform.ControlChannelName
                ControlFilterName = MappedToControlTransform.ControlFilterName
            else:
                ControlSectionNumber = ControlToVolumeTransform.ControlSectionNumber
                ControlChannelName = ControlToVolumeTransform.ControlChannelName
                ControlFilterName = ControlToVolumeTransform.ControlFilterName

            OutputTransform = OutputSectionMappingsNode.FindStosTransform(ControlSectionNumber=ControlSectionNumber,
                                                                          ControlChannelName=ControlChannelName,
                                                                          ControlFilterName=ControlFilterName,
                                                                          MappedSectionNumber=MappedToControlTransform.MappedSectionNumber,
                                                                          MappedChannelName=MappedToControlTransform.MappedChannelName,
                                                                          MappedFilterName=MappedToControlTransform.MappedFilterName)

            if OutputTransform is None:
                OutputTransform = nornir_buildmanager.volumemanager.transformnode.TransformNode(
                    attrib=MappedToControlTransform.attrib)
                OutputTransform.Name = str(mappedSectionNumber) + '-' + str(ControlSection)
                OutputTransform.SetTransform(MappedToControlTransform)
                OutputTransformAdded = OutputSectionMappingsNode.AddOrUpdateTransform(OutputTransform)
                OutputTransform.Path = OutputTransform.Name + '.stos'  # Path creates directory and the fullpath parameter is missing.  Needs to run after the transform is added                                

                # Remove any residual transform file just in case
                if os.path.exists(OutputTransform.FullPath):
                    os.remove(OutputTransform.FullPath)

            if ControlToVolumeTransform is not None:
                OutputTransform.Path = str(mappedSectionNumber) + '-' + str(
                    ControlToVolumeTransform.ControlSectionNumber) + '.stos'

            if not OutputTransform.IsInputTransformMatched(MappedToControlTransform):
                Logger.info(" %s: Removed outdated transform %s" % (logStr, OutputTransform.Path))
                if os.path.exists(OutputTransform.FullPath):
                    os.remove(OutputTransform.FullPath)

            # ===================================================================
            # if not hasattr(OutputTransform, 'InputTransformChecksum'):
            #     if os.path.exists(OutputTransform.FullPath):
            #         os.remove(OutputTransform.FullPath)
            # else:
            #     if not MappedToControlTransform.Checksum == OutputTransform.InputTransformChecksum:
            #         if os.path.exists(OutputTransform.FullPath):
            #             os.remove(OutputTransform.FullPath)
            # ===================================================================

            if ControlToVolumeTransform is None:
                # This maps directly to the origin, add it to the output stos group
                # Files.RemoveOutdatedFile(MappedToControlTransform.FullPath, OutputTransform.FullPath )

                if not os.path.exists(OutputTransform.FullPath):
                    Logger.info(" %s: Copy mapped to volume center stos transform %s" % (logStr, OutputTransform.Path))
                    shutil.copy(MappedToControlTransform.FullPath, OutputTransform.FullPath)
                    OutputTransform.ResetChecksum()
                    # OutputTransform.Checksum = MappedToControlTransform.Checksum
                    OutputTransform.SetTransform(MappedToControlTransform)

                    yield OutputSectionMappingsNode

            else:
                OutputTransform.ControlSectionNumber = ControlToVolumeTransform.ControlSectionNumber
                OutputTransform.ControlChannelName = ControlToVolumeTransform.ControlChannelName
                OutputTransform.ControlFilterName = ControlToVolumeTransform.ControlFilterName

                if hasattr(OutputTransform, "ControlToVolumeTransformChecksum"):
                    if not OutputTransform.ControlToVolumeTransformChecksum == ControlToVolumeTransform.Checksum:
                        Logger.info(" %s: ControlToVolumeTransformChecksum mismatch, removing" % logStr)
                        if os.path.exists(OutputTransform.FullPath):
                            os.remove(OutputTransform.FullPath)
                elif os.path.exists(OutputTransform.FullPath):
                    os.remove(OutputTransform.FullPath)

                if not os.path.exists(OutputTransform.FullPath):
                    try:
                        Logger.info(" %s: Adding transforms" % logStr)
                        prettyoutput.Log(logStr)
                        MToVStos = stosfile.AddStosTransforms(MappedToControlTransform.FullPath,
                                                              ControlToVolumeTransform.FullPath,
                                                              EnrichTolerance=EnrichTolerance)
                        MToVStos.Save(OutputTransform.FullPath)

                        OutputTransform.ControlToVolumeTransformChecksum = ControlToVolumeTransform.Checksum
                        OutputTransform.ResetChecksum()
                        OutputTransform.SetTransform(MappedToControlTransform)
                        # OutputTransform.Checksum = stosfile.StosFile.LoadChecksum(OutputTransform.FullPath)
                    except ValueError:
                        # Probably an invalid transform.  Skip it
                        OutputTransform.Clean()
                        OutputTransform = None
                        pass
                    yield OutputSectionMappingsNode
                else:
                    Logger.info(" %s: is still valid" % logStr)

            for retval in SliceToVolumeFromRegistrationTreeNodeRecursive(rt, mappedNode, InputGroupNode,
                                                                         OutputGroupNode,
                                                                         EnrichTolerance=EnrichTolerance,
                                                                         ControlToVolumeTransform=OutputTransform):
                yield retval


def RegistrationTreeFromStosMapNode(stos_map_node) -> registrationtree.RegistrationTree():
    rt = registrationtree.RegistrationTree()

    for mappingNode in stos_map_node.findall('Mapping'):
        for mappedSection in mappingNode.Mapped:
            rt.AddPair(mappingNode.Control, mappedSection)

    return rt


def __MappedFilterForTransform(transform_node):
    return __GetFilterAndMaskFilter(transform_node,
                                    transform_node.MappedSectionNumber,
                                    transform_node.MappedChannelName,
                                    transform_node.MappedFilterName)


def __ControlFilterForTransform(transform_node):
    return __GetFilterAndMaskFilter(transform_node,
                                    transform_node.ControlSectionNumber,
                                    transform_node.ControlChannelName,
                                    transform_node.ControlFilterName)


def __GetFilter(transform_node, section, channel, filter_name):
    block_node = transform_node.FindParent(ParentTag='Block')
    if block_node is None:
        return None
    sectionNode = block_node.GetSection(section)
    if sectionNode is None:
        return None
    channelNode = sectionNode.GetChannel(channel)
    if channelNode is None:
        return None

    filterNode = channelNode.GetFilter(filter_name)
    return filterNode


def __GetFilterAndMaskFilter(transform_node, section, channel, filter_name):
    block_node = transform_node.FindParent(ParentTag='Block')
    if block_node is None:
        return None

    sectionNode = block_node.GetSection(section)
    if sectionNode is None:
        return None, None

    channelNode = sectionNode.GetChannel(channel)
    if channelNode is None:
        return None, None

    filterNode = channelNode.GetFilter(filter_name)
    if filterNode is None:
        return None, None

    mask_filterNode = filterNode.GetMaskFilter()
    return filterNode, mask_filterNode


def __GetFirstMatchingFilter(block_node, section_number, channel_name, filter_pattern) -> FilterNode | None:
    '''Return the first filter in the section matching the pattern, or None if no filter exists'''
    section_node = block_node.GetSection(section_number)

    if section_node is None:
        Logger = logging.getLogger(__name__ + '.__GetFirstMatchingFilter')
        Logger.warning("Section %s is missing" % section_number)
        return None

    channel_node = section_node.GetChannel(channel_name)
    if channel_node is None:
        Logger = logging.getLogger(__name__ + '.__GetFirstMatchingFilter')
        Logger.warning("Channel %s.%s is missing, skipping grid refinement" % (section_number, channel_name))
        return None

    # TODO: Skip transforms using filters which no longer exist.  Should live in a separate function.
    filter_matches = nornir_buildmanager.volumemanager.SearchCollection(channel_node.Filters,
                                                                        'Name', filter_pattern,
                                                                        CaseSensitive=True)  # type: Generator[FilterNode, None, None]

    result = next(filter_matches, None)

    if result is None:
        Logger = logging.getLogger(__name__ + '.__GetFirstMatchingFilter')
        Logger.warning("No %s.%s filters match pattern %s" % (section_number, channel_node, filter_pattern))
        return None

    return result


# def __MatchMappedFiltersForTransform(InputTransformNode, channelPattern=None, filterPattern=None):
#
#     if(filterPattern is None):
#         filterPattern = InputTransformNode.MappedFilterName
#
#     if(channelPattern is None):
#         channelPattern = InputTransformNode.MappedChannelName
#
#     sectionNumber = InputTransformNode.MappedSectionNumber
#     BlockNode = InputTransformNode.FindParent(ParentTag='Block')
#     sectionNode = BlockNode.GetSection(sectionNumber)
#     return sectionNode.MatchChannelFilterPattern(channelPattern, filterPattern)
#
#
# def __MatchControlFiltersForTransform(InputTransformNode, channelPattern=None, filterPattern=None):
#
#     if(filterPattern is None):
#         filterPattern = InputTransformNode.ControlFilterName
#
#     if(channelPattern is None):
#         channelPattern = InputTransformNode.ControlChannelName
#
#     sectionNumber = InputTransformNode.ControlSectionNumber
#     BlockNode = InputTransformNode.FindParent(ParentTag='Block')
#     sectionNode = BlockNode.GetSection(sectionNumber)
#     return sectionNode.MatchChannelFilterPattern(channelPattern, filterPattern)


def ScaleStosGroup(InputStosGroupNode: StosGroupNode, OutputDownsample: int, OutputGroupName: str, UseMasks: bool,
                   **kwargs):
    '''Take a stos group node, scale the transforms, and save in new stosgroup
    
       TODO: This function used to create stos transforms between different filters to.  Port that to a separate function
    '''
    GroupParent = InputStosGroupNode.Parent

    OutputGroupNode = nornir_buildmanager.volumemanager.stosgroupnode.StosGroupNode.Create(OutputGroupName,
                                                                                           OutputDownsample)
    (SaveBlockNode, OutputGroupNode) = GroupParent.UpdateOrAddChildByAttrib(OutputGroupNode)

    os.makedirs(OutputGroupNode.FullPath, exist_ok=True)

    if SaveBlockNode:
        (yield GroupParent)

    for inputSectionMapping in InputStosGroupNode.SectionMappings:

        (SectionMappingNodeAdded, OutputSectionMapping) = OutputGroupNode.GetOrCreateSectionMapping(
            inputSectionMapping.MappedSectionNumber)
        if SectionMappingNodeAdded:
            (yield OutputGroupNode)

        InputTransformNodes = inputSectionMapping.findall('Transform')

        for InputTransformNode in InputTransformNodes:

            if not os.path.exists(InputTransformNode.FullPath):
                continue

            # ControlFilters = __ControlFiltersForTransform(InputTransformNode, ControlChannelPattern, ControlFilterPattern)
            # MappedFilters = __MappedFiltersForTransform(InputTransformNode, MappedChannelPattern, MappedFilterPattern)
            try:
                (ControlFilter, ControlMaskFilter) = __ControlFilterForTransform(InputTransformNode)
                (MappedFilter, MappedMaskFilter) = __MappedFilterForTransform(InputTransformNode)
            except AttributeError as e:
                prettyoutput.LogErr(
                    "ScaleStosGroup missing filter for InputTransformNode " + InputTransformNode.FullPath)
                continue

            if ControlFilter is None or MappedFilter is None:
                prettyoutput.LogErr(
                    "ScaleStosGroup missing filter for InputTransformNode " + InputTransformNode.FullPath)
                continue
            # for (ControlFilter, MappedFilter) in itertools.product(ControlFilters, MappedFilters):

            (stosNode_added, output_stos_node) = OutputGroupNode.GetOrCreateStosTransformNode(ControlFilter,
                                                                                              MappedFilter,
                                                                                              OutputType=InputTransformNode.Type,
                                                                                              OutputPath=nornir_buildmanager.volumemanager.stosgroupnode.StosGroupNode.GenerateStosFilename(
                                                                                                  ControlFilter,
                                                                                                  MappedFilter))

            if not stosNode_added:
                if not output_stos_node.IsInputTransformMatched(InputTransformNode):
                    try:
                        os.remove(output_stos_node.FullPath)
                    except FileNotFoundError:
                        pass  # It is OK if the file doesn't exist if we tried to delete it
            else:
                # Remove an old file if we had to generate the meta-data
                try:
                    os.remove(output_stos_node.FullPath)
                except FileNotFoundError:
                    pass  # It is OK if the file doesn't exist if we tried to delete it

            if not os.path.exists(output_stos_node.FullPath):
                try:
                    stosGenerated = __GenerateStosFile(InputTransformNode,
                                                       output_stos_node.FullPath,
                                                       OutputDownsample,
                                                       ControlFilter,
                                                       MappedFilter,
                                                       UseMasks=None)

                    if stosGenerated is not None:
                        stosGenerated.Save(output_stos_node.FullPath)
                    else:
                        shutil.copyfile(InputTransformNode.FullPath, output_stos_node.FullPath)

                    output_stos_node.ResetChecksum()
                    output_stos_node.SetTransform(InputTransformNode)
                except FileNotFoundError:
                    OutputGroupNode.remove(output_stos_node)

                (yield OutputGroupNode)


def LinearBlendStosGroup(InputStosGroupNode: StosGroupNode, OutputGroupName: str,
                         linear_blend_factor: float | None,
                         travel_limit: float | None,  
                         ignore_rotation: bool = False, **kwargs):
    '''Take a stos group node, convert each transform to a rigid linear transform, blend in the linear
       transform with the control points of the original transform to "flatten" it
    '''
    GroupParent = InputStosGroupNode.Parent
    OutputDownsample = InputStosGroupNode.Downsample

    OutputGroupNode = nornir_buildmanager.volumemanager.stosgroupnode.StosGroupNode.Create(OutputGroupName,
                                                                                           OutputDownsample)
    (SaveBlockNode, OutputGroupNode) = GroupParent.UpdateOrAddChildByAttrib(OutputGroupNode)

    os.makedirs(OutputGroupNode.FullPath, exist_ok=True)

    if SaveBlockNode:
        (yield GroupParent)

    for inputSectionMapping in InputStosGroupNode.SectionMappings:

        (SectionMappingNodeAdded, OutputSectionMapping) = OutputGroupNode.GetOrCreateSectionMapping(
            inputSectionMapping.MappedSectionNumber)
        if SectionMappingNodeAdded:
            (yield OutputGroupNode)

        InputTransformNodes = inputSectionMapping.findall('Transform')

        for InputTransformNode in InputTransformNodes:
            if not os.path.exists(InputTransformNode.FullPath):
                continue

            # ControlFilters = __ControlFiltersForTransform(InputTransformNode, ControlChannelPattern, ControlFilterPattern)
            # MappedFilters = __MappedFiltersForTransform(InputTransformNode, MappedChannelPattern, MappedFilterPattern)
            try:
                (ControlFilter, ControlMaskFilter) = __ControlFilterForTransform(InputTransformNode)
                (MappedFilter, MappedMaskFilter) = __MappedFilterForTransform(InputTransformNode)
            except AttributeError as e:
                prettyoutput.LogErr(
                    "ScaleStosGroup missing filter for InputTransformNode " + InputTransformNode.FullPath)
                continue

            if ControlFilter is None or MappedFilter is None:
                prettyoutput.LogErr(
                    "ScaleStosGroup missing filter for InputTransformNode " + InputTransformNode.FullPath)
                continue
            # for (ControlFilter, MappedFilter) in itertools.product(ControlFilters, MappedFilters):

            (stosNode_added, output_stos_node) = OutputGroupNode.GetOrCreateStosTransformNode(ControlFilter,
                                                                                              MappedFilter,
                                                                                              OutputType=InputTransformNode.Type,
                                                                                              OutputPath=nornir_buildmanager.volumemanager.stosgroupnode.StosGroupNode.GenerateStosFilename(
                                                                                                  ControlFilter,
                                                                                                  MappedFilter))

            if not stosNode_added:
                if not (output_stos_node.IsInputTransformMatched(InputTransformNode) and
                        output_stos_node.linear_blend_factor == InputTransformNode.linear_blend_factor):
                    try:
                        os.remove(output_stos_node.FullPath)
                    except FileNotFoundError:
                        pass  # It is OK if the file doesn't exist if we tried to delete it
            else:
                # Remove an old file if we had to generate the meta-data
                try:
                    os.remove(output_stos_node.FullPath)
                except FileNotFoundError:
                    pass  # It is OK if the file doesn't exist if we tried to delete it

            if not os.path.exists(output_stos_node.FullPath):
                try:
                    shutil.copyfile(InputTransformNode.FullPath, output_stos_node.FullPath)
                    loaded_output_stos = nornir_imageregistration.files.StosFile.Load(output_stos_node.FullPath)
                    transform_changed = loaded_output_stos.BlendWithLinear(linear_factor=linear_blend_factor,
                                                                           travel_limit=travel_limit,
                                                                           ignore_rotation=ignore_rotation)
                    output_stos_node.linear_blend_factor = linear_blend_factor

                    if transform_changed:
                        loaded_output_stos.Save(output_stos_node.FullPath)

                    output_stos_node.ResetChecksum()
                    output_stos_node.SetTransform(InputTransformNode)

                except FileNotFoundError:
                    OutputGroupNode.remove(output_stos_node)

                (yield OutputGroupNode)


def __RemoveStosFileIfOutdated(OutputStosNode, InputStosNode):
    '''Removes the .stos file from the file system but leaves the meta data alone for reuse.
       Always removes the file if the meta-data does not have an InputTransformChecksum property'''

    if hasattr(OutputStosNode, "InputTransformChecksum"):
        if not transforms.IsValueMatched(OutputNode=OutputStosNode,
                                         OutputAttribute="InputTransformChecksum",
                                         TargetValue=InputStosNode.Checksum):
            if os.path.exists(OutputStosNode.FullPath):
                os.remove(OutputStosNode.FullPath)
                return True
        else:
            # InputTransformChecksum is equal
            return False

    elif os.path.exists(OutputStosNode.FullPath):
        os.remove(OutputStosNode.FullPath)
        return True

    return False


def _GetOrCreateStosToMosaicTransform(StosTransformNode, transform_node: TransformNode, OutputTransformName: str):
    OutputTransformNode = transform_node.Parent.GetTransform(OutputTransformName)
    added = False
    if OutputTransformNode is None:
        # Create transform node for the output
        OutputTransformNode = nornir_buildmanager.volumemanager.TransformNode.Create(Name=OutputTransformName,
                                                                                     Type="MosaicToVolume_Untranslated",
                                                                                     Path=OutputTransformName + '.mosaic')
        transform_node.Parent.AddChild(OutputTransformNode)
        added = True

    return added, OutputTransformNode


def _ApplyStosToMosaicTransform(StosTransformNode: TransformNode | None, transform_node: TransformNode, OutputTransformName: str, Logger, **kwargs):
    '''
    return: Transform node if there was an create/update.  None if no change
    '''

    MappedFilterNode = transform_node.FindParent('Filter')

    (added_output_transform, OutputTransformNode) = _GetOrCreateStosToMosaicTransform(StosTransformNode, transform_node,
                                                                                      OutputTransformName)

    if added_output_transform:
        OutputTransformNode.SetTransform(StosTransformNode)
        OutputTransformNode.InputMosaicTransformChecksum = transform_node.Checksum
    else:
        OutputTransformNode.Type = "MosaicToVolume_Untranslated"

    if StosTransformNode is not None:
        if not (OutputTransformNode.IsInputTransformMatched(
                StosTransformNode) and OutputTransformNode.InputMosaicTransformChecksum == transform_node.Checksum):
            try:
                os.remove(OutputTransformNode.FullPath)
            except FileNotFoundError:
                pass
    else:
        if not OutputTransformNode.InputMosaicTransformChecksum == transform_node.Checksum:
            try:
                os.remove(OutputTransformNode.FullPath)
            except FileNotFoundError:
                pass

    if os.path.exists(OutputTransformNode.FullPath):
        return OutputTransformNode

    if StosTransformNode is None:
        # No transform, copy transform directly

        # Create transform node for the output
        shutil.copyfile(transform_node.FullPath, OutputTransformNode.FullPath)
        # OutputTransformNode.SetTransform(StosTransformNode)
        OutputTransformNode.InputMosaicTransformChecksum = transform_node.Checksum
        # OutputTransformNode.Checksum = TransformNode.Checksum

    else:
        # files.RemoveOutdatedFile(StosTransformNode.FullPath, OutputTransformNode.FullPath)

        stos_group_node = StosTransformNode.FindParent('StosGroup')

        SToV = stosfile.StosFile.Load(StosTransformNode.FullPath)
        # Make sure we are not using a downsampled transform
        SToV = SToV.ChangeStosGridPixelSpacing(stos_group_node.Downsample, 1.0,
                                               SToV.ControlImageFullPath,
                                               SToV.MappedImageFullPath,
                                               SToV.ControlMaskFullPath,
                                               SToV.MappedMaskFullPath,
                                               create_copy=False)
        StoVTransform = factory.LoadTransform(SToV.Transform)

        MosaicTransform = mosaic.Mosaic.LoadFromMosaicFile(transform_node.FullPath)
        assert (MosaicTransform.FixedBoundingBox.BottomLeft[0] == 0 and MosaicTransform.FixedBoundingBox.BottomLeft[
            1] == 0)
        # MosaicTransform.TranslateToZeroOrigin() 
        Tasks = []

        UsePool = True
        if UsePool:
            # This is a parallel operation, but the Python GIL is so slow using threads is slower.
            Pool = nornir_pools.GetLocalMachinePool()

            for imagename, MosaicToSectionTransform in list(MosaicTransform.ImageToTransform.items()):
                task = Pool.add_task(imagename, nornir_imageregistration.transforms.AddTransforms, StoVTransform,
                                     MosaicToSectionTransform)
                task.imagename = imagename
                if hasattr(MosaicToSectionTransform, 'gridWidth'):
                    task.dimX = MosaicToSectionTransform.gridWidth
                if hasattr(MosaicToSectionTransform, 'gridHeight'):
                    task.dimY = MosaicToSectionTransform.gridHeight

                Tasks.append(task)

            for task in Tasks:
                try:
                    MosaicToVolume = task.wait_return()
                    MosaicTransform.ImageToTransform[task.imagename] = MosaicToVolume
                except:
                    Logger.warning("Exception transforming tile. Skipping %s" % task.imagename)
                    pass
        else:
            for imagename, MosaicToSectionTransform in list(MosaicTransform.ImageToTransform.items()):
                MosaicToVolume = StoVTransform.AddTransform(MosaicToSectionTransform)
                MosaicTransform.ImageToTransform[imagename] = MosaicToVolume

        if len(MosaicTransform.ImageToTransform) > 0:
            OutputMosaicFile = MosaicTransform.ToMosaicFile()
            OutputMosaicFile.Save(OutputTransformNode.FullPath)

            OutputTransformNode.ResetChecksum()
            OutputTransformNode.SetTransform(StosTransformNode)
            OutputTransformNode.InputMosaicTransformChecksum = transform_node.Checksum

    return OutputTransformNode


def BuildMosaicToVolumeTransforms(stos_map_node: StosMapNode, stos_group_node: StosGroupNode, block_node: BlockNode,
                                  ChannelsRegEx: str, InputTransformName: str, OutputTransformName: str, Logger,
                                  **kwargs):
    '''Create a .mosaic file that translates a section directly into the volume.  Two .mosaics are created, a _untraslated version which may have a negative origin
       and a version with the requested OutputTransformName which will have an origin at zero
    '''
    Channels = block_node.findall('Section/Channel')

    MatchingChannelNodes = nornir_buildmanager.volumemanager.SearchCollection(Channels, 'Name',
                                                                              ChannelsRegEx)  # type: Generator[ChannelNode, None, None]

    StosMosaicTransforms = []  # type: list[TransformNode]

    UntranslatedOutputTransformName = OutputTransformName + "_Untranslated"

    for channelNode in MatchingChannelNodes:
        transform_node = channelNode.GetTransform(InputTransformName)

        if transform_node is None:
            continue

        node_to_save = BuildChannelMosaicToVolumeTransform(stos_map_node, stos_group_node, transform_node,
                                                           UntranslatedOutputTransformName, Logger, **kwargs)
        if node_to_save is not None:
            yield node_to_save

        output_transform_node = channelNode.GetChildByAttrib('Transform', 'Name', UntranslatedOutputTransformName)
        if output_transform_node is not None:
            StosMosaicTransforms.append(output_transform_node)

    if len(StosMosaicTransforms) == 0:
        return

    __MoveMosaicsToZeroOrigin(StosMosaicTransforms, OutputTransformName)

    yield block_node


def __MoveMosaicsToZeroOrigin(StosMosaicTransforms: Iterable[TransformNode], OutputStosMosaicTransformName: str):
    '''Given a set of transforms, ensure they are all translated so that none have a negative coordinate for the origin.
       :param list StosMosaicTransforms: [StosTransformNode]
       :param list OutputStosMosaicTransformName: list of names for output nodes
       '''

    output_transform_list = []

    for input_transform_node in StosMosaicTransforms:
        channel_node = input_transform_node.Parent
        output_transform_node = channel_node.GetTransform(OutputStosMosaicTransformName)
        if output_transform_node is None:
            output_transform_node = input_transform_node.Copy()
            output_transform_node.Name = OutputStosMosaicTransformName
            output_transform_node.Path = OutputStosMosaicTransformName + '.mosaic'

            channel_node.AddChild(output_transform_node)
        else:
            if not output_transform_node.IsInputTransformMatched(input_transform_node):
                if os.path.exists(output_transform_node.FullPath):
                    os.remove(output_transform_node.FullPath)

        output_transform_list.append(output_transform_node)

        # Always copy so our offset calculation is based on untranslated transforms
        output_transform_node.Type = "MosaicToVolume"
        output_transform_node.SetTransform(input_transform_node)
        shutil.copy(input_transform_node.FullPath, output_transform_node.FullPath)

    mosaicToVolume = mosaicvolume.MosaicVolume.Load(output_transform_list)

    # Translate needs to accound for the fact that the mosaics need an origin of 0,0 for assemble to work.  We also need to figure out the largest image dimension
    # and set the CropBox property so each image is the same size after assemble is used.
    translation_to_zero = mosaicToVolume.TranslateToZeroOrigin()

    if translation_to_zero[0] == 0 and translation_to_zero[1] == 0:
        return None

    new_bbox = mosaicToVolume.VolumeBounds

    (minY, minX, maxY, maxX) = new_bbox.ToTuple()

    maxX = int(math.ceil(maxX))
    maxY = int(math.ceil(maxY))

    # Failing these asserts means the translate to zero origin function is not actually translating to a zero origin
    assert (minX >= 0)
    assert (minY >= 0)

    for transform in output_transform_list:
        transform.CropBox = (maxX, maxY)

    # Create a new node for the translated mosaic if needed and save it

    mosaicToVolume.Save()

    return


def FetchVolumeTransforms(stos_map_node: StosMapNode, ChannelsRegEx: str | None, TransformRegEx: str | None):
    block_node = stos_map_node.FindParent('Block')
    Channels = block_node.findall('Section/Channel')
    MatchingChannelNodes = nornir_buildmanager.volumemanager.SearchCollection(Channels, 'Name', ChannelsRegEx)

    MatchingTransformNodes = nornir_buildmanager.volumemanager.SearchCollection(MatchingChannelNodes, 'Name',
                                                                                TransformRegEx)

    StosMosaicTransforms = []
    for transform_node in MatchingTransformNodes:
        sectionNode = transform_node.FindParent('Section')
        if sectionNode is None:
            continue

        if not stos_map_node.SectionInMap(sectionNode.Number):
            continue

        StosMosaicTransforms.append(transform_node)

    return StosMosaicTransforms


def ReportVolumeBounds(stos_map_node: StosMapNode, ChannelsRegEx: str, TransformName: str, Logger, **kwargs):
    StosMosaicTransformNodes = FetchVolumeTransforms(stos_map_node, ChannelsRegEx, TransformName)

    StosMosaicTransforms = [tnode.FullPath for tnode in StosMosaicTransformNodes]

    mosaicToVolume = mosaicvolume.MosaicVolume.Load(StosMosaicTransforms)

    return str(mosaicToVolume.VolumeBounds)


def BuildChannelMosaicToVolumeTransform(stos_map_node: StosMapNode,
                                        stos_group_node: StosGroupNode,
                                        transform_node: TransformNode,
                                        OutputTransformName: str,
                                        Logger, **kwargs):
    '''Build a slice-to-volume transform for each section referenced in the StosMap'''

    MosaicTransformParent = transform_node.Parent

    SaveTransformParent = False

    MappedChannelNode = transform_node.FindParent('Channel')

    section_node = transform_node.FindParent('Section')
    if section_node is None:
        Logger.error("No section found for transform: " + str(transform_node))
        return None

    MappedSectionNumber = section_node.Number

    ControlSectionNumbers = list(stos_map_node.FindAllControlsForMapped(MappedSectionNumber))
    if len(ControlSectionNumbers) == 0:
        if stos_map_node.CenterSection != MappedSectionNumber:
            Logger.info("No SectionMappings found for section: " + str(MappedSectionNumber))

    Logger.info("%d -> %s" % (MappedSectionNumber, str(ControlSectionNumbers)))

    SectionMappingNode = stos_group_node.GetSectionMapping(MappedSectionNumber)
    if SectionMappingNode is None:
        stosMosaicTransform = _ApplyStosToMosaicTransform(None, transform_node, OutputTransformName, Logger, **kwargs)
        if stosMosaicTransform is not None:
            SaveTransformParent = True

    else:
        for stostransform in SectionMappingNode.Transforms:
            if not stostransform.MappedChannelName == MappedChannelNode.Name:
                continue

            if not int(stostransform.ControlSectionNumber) in ControlSectionNumbers:
                continue

            stosMosaicTransform = _ApplyStosToMosaicTransform(stostransform, transform_node, OutputTransformName,
                                                              Logger,
                                                              **kwargs)
            if stosMosaicTransform is not None:
                SaveTransformParent = True

    #         mosaicToVolume = mosaicvolume.MosaicVolume.Load(StosMosaicTransforms)
    #         mosaicToVolume.TranslateToZeroOrigin()
    #         mosaicToVolume.Save()

    #
    #     SliceToVolumeTransform = FindTransformForMapping(StosGroupNode, ControlSectionNumber, MappedSectionNumber)
    #     if SliceToVolumeTransform is None:
    #         Logger.error("No SliceToVolumeTransform found for: " + str(MappedSectionNumber) + " -> " + ControlSectionNumber.Control)
    #         return
    #
    #     files.RemoveOutdatedFile(SliceToVolumeTransform.FullPath, OutputTransformNode.FullPath)
    #
    #     if os.path.exists(OutputTransformNode.FullPath):
    #         return
    if SaveTransformParent:
        return MosaicTransformParent

    return None


if __name__ == '__main__':
    pass
