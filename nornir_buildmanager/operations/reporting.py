'''
Created on Oct 3, 2012

@author: u0490822
'''

import datetime
import logging
import math
import os
import shutil
import nornir_buildmanager

from nornir_buildmanager.pipelinemanager import PipelineManager, ArgumentSet 
import nornir_imageregistration.core
import nornir_shared.images
import nornir_shared.plot
import nornir_shared.prettyoutput

import nornir_buildmanager.importers.serialemlog as serialemlog
import nornir_buildmanager.importers.idoc as idoc
import nornir_pools
import nornir_shared.files as nfiles
from nornir_buildmanager.exceptions import NornirUserException
from math import ceil

# import Pipelines.VolumeManagerETree as VolumeManager
if __name__ == '__main__':
    pass


class RowList(list):
    '''class used for HTML to place into rows'''
    pass


class ColumnList(list):
    '''Class used for HTML to place into columns'''

    def __init__(self, *args):
        self._caption = None
        self.extend(args)

    @property
    def caption(self):
        if hasattr(self, '_caption'):
            return self._caption

        return None

    @caption.setter
    def caption(self, val):
        self._caption = val


class UnorderedItemList(list):
    '''Class used for HTML to create unordered list from items'''
    pass


class HTMLBuilder(list):
    '''A list of strings that contain HTML'''

    @property
    def IndentLevel(self):
        return self.__IndentLevel

    def Indent(self):
        self.__IndentLevel += 1

    def Dedent(self):
        self.__IndentLevel -= 1

    def __init__(self, indentlevel=None):
        super(HTMLBuilder, self).__init__()

        if indentlevel is None:
            self.__IndentLevel = 0
        else:
            self.__IndentLevel = indentlevel

    def __IndentString(self, indent):

        if indent is None:
            return ''

        return ' ' * indent

    def Add(self, value):
        if isinstance(value, list):
            self.extend(value)
        elif isinstance(value, str):
            self.append(self.__IndentString(self.IndentLevel) + value)

    def __str__(self):
        return ''.join([x for x in self])


class HTMLPaths(object):

    @property
    def SourceRootDir(self):
        return self._SourceRootDir

    @property
    def OutputDir(self):
        return self._OutputDir

    @property
    def OutputFile(self):
        return self._OutputFile
    
    def OutputPage(self, page=None):
        if page is None or page == 0:
            return self._OutputFile
        else: 
            return f"{self._OutputPagePrefix}_{page}.html"

    @property
    def ThumbnailDir(self):
        return self._ThumbnailDir

    @property
    def ThumbnailRelative(self):
        return self._ThumbnialRootRelative

    def __init__(self, RootElementPath, OutputFileFullPath):

        if OutputFileFullPath is None:
            OutputFileFullPath = "DefaultReport.html"

        self._SourceRootDir = RootElementPath
        if not isinstance(RootElementPath, str):
            self._SourceRootDir = RootElementPath.Path

        self._OutputDir = os.path.dirname(OutputFileFullPath)
        self._OutputDir.strip()
        
        if len(self.OutputDir) == 0:
            self._OutputDir = RootElementPath
            self._OutputFile = os.path.basename(OutputFileFullPath)
        else:
            self._OutputFile = os.path.basename(OutputFileFullPath)
            
        (filename, _) = os.path.splitext(self._OutputFile)
        
        self._OutputPagePrefix = f"{filename}_"

        (self._ThumbnialRootRelative, self._ThumbnailDir) = self.__ThumbnailPaths()

    def CreateOutputDirs(self):
        os.makedirs(self.OutputDir, exist_ok=True)
        os.makedirs(self.ThumbnailDir, exist_ok=True)

    @classmethod
    def __StripLeadingPathSeperator(cls, path):
        while(path[0] == os.sep or path[0] == os.altsep):
            path = path[1:]

        return path

    def GetSubNodeRelativePath(self, subpath):

        fullpath = subpath
        if not isinstance(fullpath, str):
            fullpath = subpath.FullPath

        RelativePath = fullpath.replace(self.SourceRootDir, '')
        RelativePath = os.path.dirname(RelativePath)
        RelativePath = HTMLPaths.__StripLeadingPathSeperator(RelativePath)

        return RelativePath

    def GetSubNodeFullPath(self, subpath):

        RelPath = self.GetSubNodeRelativePath(subpath)
        if not RelPath is None:
            FullPath = os.path.join(RelPath, subpath.Path)
            FullPath = HTMLPaths.__StripLeadingPathSeperator(FullPath)
        else:
            return subpath.FullPath

        return FullPath

    def __ThumbnailPaths(self):
        '''Return relative and absolute thumbnails for an OutputFile'''
        (ThumbnailDirectory, ext) = os.path.splitext(self.OutputFile)
        ThumbnailDirectoryFullPath = os.path.join(self.OutputDir, ThumbnailDirectory)

        return (ThumbnailDirectory, ThumbnailDirectoryFullPath)

    def GetFileAnchorHTML(self, Node, text):
        '''Returns an <A> anchor node pointing at the nodes file.  If the file does not exist an empty string is returned'''
        # Temp patch
        FullPath = None
        patchedPath = _patchupNotesPath(Node)
        FullPath = os.path.join(Node.Parent.FullPath, patchedPath)

        RelPath = self.GetSubNodeRelativePath(FullPath)

        (junk, ext) = os.path.splitext(patchedPath)
        ext = ext.lower()

        HTML = ""

        if os.path.exists(FullPath):
            SrcFullPath = os.path.join(RelPath, patchedPath)
            HTML += HTMLAnchorTemplate % {'href': SrcFullPath, 'body': text}

        return HTML


HTMLImageTemplate = '<img src="%(src)s" alt="%(AltText)s" width="%(ImageWidth)s" height="%(ImageHeight)s" />'
HTMLAnchorTemplate = '<a href="%(href)s">%(body)s</a>'

# We add this to thumbnails and other generated files to prevent name collisions
TempFileSalt = 0


def GetTempFileSaltString(node=None) -> str:
    saltString = None
        
    if isinstance(node, nornir_buildmanager.volumemanager.XElementWrapper):
        b_node = node.FindParent('Block')
        s_node = node.FindParent('Section')
        c_node = node.FindParent('Channel')
        f_node = node.FindParent('Filter')
        
        if b_node is not None: 
            saltString = b_node.Name
        if s_node is not None: 
            saltString += f"_{s_node.Number}"
        if c_node is not None: 
            saltString += f"_{c_node.Name}"
        if f_node is not None: 
            saltString += f"_{f_node.Name}"
        
        if hasattr(node, 'Checksum'):
            saltString += "_" + node.Checksum + "_"
            
        return saltString
            
    if saltString is None:
        global TempFileSalt
    
        saltString = str(TempFileSalt) + "_"
        TempFileSalt = TempFileSalt + 1
    
        return saltString


def CopyFiles(DataNode, OutputDir=None, Move=False, **kwargs):

    if OutputDir is None:
        return

    logger = logging.getLogger(__name__ + '.CopyFiles')

    os.makedirs(OutputDir, exist_ok=True)

    if os.path.exists(DataNode.FullPath):

        if os.path.isfile(DataNode.FullPath):
            OutputFileFullPath = os.path.join(OutputDir, DataNode.Path)
            nfiles.RemoveOutdatedFile(DataNode.FullPath, OutputFileFullPath)

            if not os.path.exists(OutputFileFullPath):

                logger.info(DataNode.FullPath + " -> " + OutputFileFullPath)
                shutil.copyfile(DataNode.FullPath, OutputFileFullPath)
        else:
            # Just copy the directory over, this is an odd case
            logger.info("Copy directory " + DataNode.FullPath + " -> " + OutputDir)
            shutil.copy(DataNode.FullPath, OutputDir)


def _AbsoluePathFromRelativePath(node, path):
    '''If path is relative then make it relative from the directory containing the volume_node'''

    if not os.path.isabs(path):
        volume_dir = node.Root.FullPath
        return os.path.join(volume_dir, '..', path)
    else:
        return path


def CopyImage(FilterNode, Downsample=1.0, OutputDir=None, Move=False, **kwargs):

    if OutputDir is None:
        return

    OutputDir = _AbsoluePathFromRelativePath(FilterNode, OutputDir)
    logger = logging.getLogger(__name__ + '.CopyImage')

    os.makedirs(OutputDir, exist_ok=True)

    # Find the imageset for the DataNode
    saveImageSet = False
    ImageNode = FilterNode.GetImage(Downsample)
    if ImageNode is None:
        try:
            ImageNode = FilterNode.GetOrCreateImage(Downsample)
        except NornirUserException as e:
            logger.warning("Unable to find or generate image to be copied: {0}".format(FilterNode.FullPath))
            return None
            
        saveImageSet = True
         
    if os.path.exists(ImageNode.FullPath): 
        if os.path.isfile(ImageNode.FullPath):
            OutputFileFullPath = os.path.join(OutputDir, ImageNode.Path)
            nfiles.RemoveOutdatedFile(ImageNode.FullPath, OutputFileFullPath)

            if not os.path.exists(OutputFileFullPath):

                logger.info(ImageNode.FullPath + " -> " + OutputFileFullPath)
                shutil.copyfile(ImageNode.FullPath, OutputFileFullPath)
        else:
            # Just copy the directory over, this is an odd case
            logger.info("Copy directory " + ImageNode.FullPath + " -> " + OutputDir)
            shutil.copy(ImageNode.FullPath, OutputDir)
        
    if saveImageSet:
        return FilterNode.Imageset
    
    return None


def MoveFiles(DataNode, OutputDir, Move=False, **kwargs):
    if OutputDir is None:
        return

    os.makedirs(OutputDir, exist_ok=True)

    if os.path.exists(DataNode.FullPath):
        shutil.move(DataNode.FullPath, OutputDir)

    return None


def _patchupNotesPath(NotesNode):
    '''Temp fix for old notes elements without a path attribute'''
    if 'SourceFilename' in NotesNode.attrib:
        return NotesNode.SourceFilename
    elif 'Path' in NotesNode.attrib:
        return NotesNode.Path
    else:
        raise Exception('No path data for notes node')


def __RemoveRTFBracket(rtfStr):
    '''Removes brackets from rtfStr in pairs'''

    assert(rtfStr[0] == '{')
    rtfStr = rtfStr[1:]
    rbracket = rtfStr.find('}')
    lbracket = rtfStr.find('{')

    if lbracket < 0:
        return rtfStr[rbracket + 1:]

    if rbracket < 0:
        '''No paired bracket, remove left bracket and return'''
        return rtfStr

    if lbracket < rbracket:
        rtfStr = rtfStr[:lbracket] + __RemoveRTFBracket(rtfStr[lbracket:])
        return __RemoveRTFBracket('{' + rtfStr)
    else:
        return rtfStr[rbracket + 1:]


def __RTFToHTML(rtfStr):
    '''Crudely convert a rich-text string to html'''

    translationTable = {'\pard': '<br\>',
                        '\par': '<br\>',
                        '\viewkind': ''}

    if(rtfStr[0] == '{'):
        rtfStr = rtfStr[1:-2]

    HTMLOut = HTMLBuilder()

    HTMLOut.Add('<p>')

    translatekeys = list(translationTable.keys())
    translatekeys = sorted(translatekeys, key=len, reverse=True)

    while len(rtfStr) > 0:

        if '{' == rtfStr[0]:
            rtfStr = __RemoveRTFBracket(rtfStr)
            continue

        for key in translatekeys:
            if rtfStr.startswith(key):
                HTMLOut.Add(translationTable[key])
                rtfStr = rtfStr[len(key):]
                continue

        if rtfStr.startswith('\\'):
            rtfStr = rtfStr[1:]
            iSlash = rtfStr.find('\\')
            iSpace = rtfStr.find(' ')
            iBracket = rtfStr.find('{')

            indicies = [iSlash, iSpace, iBracket]
            goodIndex = []
            for i in indicies:
                if i > 0:
                    goodIndex.append(i)

            if len(goodIndex) == 0:
                # The string is empty, stop the loop
                break

            iClip = min(goodIndex)

            rtfStr = rtfStr[iClip:]
        else:
            HTMLOut.Add(rtfStr[0])
            rtfStr = rtfStr[1:]

    outStr = str(HTMLOut).strip()
    while outStr.endswith('<br\>'):
        outStr = outStr[:-len('<br\>')].strip()

    if len(HTMLOut) > 0:
        return '<p>' + outStr

    return ''


def HTMLFromNotesNode(DataNode, htmlPaths, **kwargs):

    # Temp patch
    HTML = htmlPaths.GetFileAnchorHTML(DataNode, "Notes: ")

    if not (DataNode.text is None or len(DataNode.text) == 0):
        (junk, ext) = os.path.splitext(_patchupNotesPath(DataNode))

        if 'rtf' in ext or 'doc' in ext:
            HTML += __RTFToHTML(DataNode.text)
        else:
            HTML += DataNode.text

    if len(HTML) == 0:
        return None

    return HTML


def __ExtractLogDataText(Data):

    DriftRows = RowList()
    CaptureTime = RowList()
    AvgTimeRows = RowList()
    MinTimeRows = RowList()
    MetaRows = RowList()
    Columns = ColumnList()

    DriftRows.append('<u class="logheader">Tile Drift</u>')
    CaptureTime.append('<u class="logheader">Overall Time</u>')
    AvgTimeRows.append('<u class="logheader">Average Tile Time</u>')
    MinTimeRows.append('<u class="logheader">Fastest Tile Time</u>')
    MetaRows.append('<u class="logheader">Information</u>')
    
    if hasattr(Data, 'AverageTileDrift'):
        DriftRows.append(['Average:', '<b>%.3g nm/sec</b>' % float(Data.AverageTileDrift)])

    if hasattr(Data, 'MinTileDrift'):
        DriftRows.append(['Min:', '%.3g nm/sec' % float(Data.MinTileDrift)])

    if hasattr(Data, 'MaxTileDrift'):
        DriftRows.append(['Max:', '%.3g nm/sec' % float(Data.MaxTileDrift)])

    if hasattr(Data, 'AverageTileTime'):
        AvgTimeRows.append(['Average:', '<b>%.3g sec/tile</b>' % float(Data.AverageTileTime)])
        
    if hasattr(Data, 'AverageSettleTime'):
        AvgTimeRows.append(['Focus & Settle:', '<b>%.3g sec/tile</b>' % float(Data.AverageSettleTime)])
        
    if hasattr(Data, 'AverageAcquisitionTime'):
        AvgTimeRows.append(['Acquisition:', '<b>%.3g sec/tile</b>' % float(Data.AverageAcquisitionTime)])

    if hasattr(Data, 'FastestTileTime'):
        MinTimeRows.append(['Overall:', '%.3g sec' % Data.FastestTileTime])
        
    if hasattr(Data, 'FastestSettleTime'):
        MinTimeRows.append(['Settle:', '%.3g sec' % Data.FastestSettleTime])
        
    if hasattr(Data, 'FastestAcquisitionTime') and Data.FastestAcquisitionTime is not None:
        MinTimeRows.append(['Acquisition:', '%.3g sec' % Data.FastestAcquisitionTime])
    
    if hasattr(Data, 'NumTiles'):
        MetaRows.append(['Number of tiles:', str(Data.NumTiles)])

    if hasattr(Data, 'TotalTime'):
        dtime = datetime.timedelta(seconds=round(float(Data.TotalTime)))
        MetaRows.append(['Total Time:', '<b>' + str(dtime) + '</b>'])
        
    if hasattr(Data, 'MontageEnd'):
        dtime = datetime.timedelta(seconds=round(float(Data.MontageEnd - Data.StartupTimeStamp)))
        MetaRows.append(['Total + Setup:', '<b>' + str(dtime) + '</b>'])
        
    if hasattr(Data, 'StartupDateTime'):
        MetaRows.append(['Capture Date:', '<b>' + str(Data.StartupDateTime) + '</b>'])

    if hasattr(Data, 'Version'):
        MetaRows.append(['Version:', str(Data.Version)])
        
    if hasattr(Data, 'CaptureSetupTime'):
        time_val = float(Data.CaptureSetupTime)
        if time_val > 0:
            dtime = datetime.timedelta(seconds=round(time_val))
            CaptureTime.append(['Setup (est.):', str(dtime)])
        else:
            CaptureTime.append(['Setup (est.):', ''])
        
    if hasattr(Data, 'LowMagCookTime'):
        time_val = float(Data.LowMagCookTime)
        if time_val > 0:
            dtime = datetime.timedelta(seconds=round(time_val))
            CaptureTime.append(['Low Mag Cook:', str(dtime)])
        else:
            CaptureTime.append(['Low Mag Cook:', ''])
            
    if hasattr(Data, 'HighMagCookTime'):
        time_val = float(Data.HighMagCookTime)
        if time_val > 0:
            dtime = datetime.timedelta(seconds=round(time_val))
            CaptureTime.append(['High Mag Cook:', str(dtime)])
        else:
            CaptureTime.append(['High Mag Cook:', ''])
        
    if hasattr(Data, 'FilamentStabilizationTime'):
        time_val = float(Data.FilamentStabilizationTime)
        if time_val > 0:
            dtime = datetime.timedelta(seconds=round(0))
            CaptureTime.append(['Filament Stable:', str(dtime)])
        else:
            CaptureTime.append(['Filament Stable:', ''])
        
    if hasattr(Data, 'TotalTileAcquisitionTime'):
        time_val = float(Data.TotalTileAcquisitionTime)
        if time_val > 0:
            dtime = datetime.timedelta(seconds=round(time_val))
            CaptureTime.append(['Tile Capture:', str(dtime)])
        else:
            CaptureTime.append(['Tile Capture:', ''])
 
    if not MetaRows is None:
        Columns.append(MetaRows)
         
    if not CaptureTime is None:
        Columns.append(CaptureTime)
        
    if not AvgTimeRows is None:
        Columns.append(AvgTimeRows)
        
    if not MinTimeRows is None:
        Columns.append(MinTimeRows)

    if not DriftRows is None:
        Columns.append(DriftRows)
 
    return Columns


def HTMLFromDataNode(DataNode, htmlpaths, MaxImageWidth=None, MaxImageHeight=None, **kwargs):

    if not hasattr(DataNode, 'Name'):
        return

    if DataNode.Name == 'Log':
        return HTMLFromLogDataNode(DataNode, htmlpaths, MaxImageWidth, MaxImageHeight, **kwargs)
    elif DataNode.Name == 'IDoc':
        return HTMLFromIDocDataNode(DataNode, htmlpaths, MaxImageWidth, MaxImageHeight, **kwargs)
    else:
        return HTMLFromUnknownDataNode(DataNode, htmlpaths, MaxImageWidth, MaxImageHeight, **kwargs)


def HTMLFromUnknownDataNode(DataNode, htmlpaths, MaxImageWidth=None, MaxImageHeight=None, **kwargs):

    Name = "Data"
    if hasattr(DataNode, 'Name'):
        Name = DataNode.Name

    return htmlpaths.GetFileAnchorHTML(DataNode, Name)


def __ExtractIDocDataText(DataNode):

    rows = RowList()
    
    try:
    
        if 'ExposureTime' in DataNode.attrib:
            rows.append(['Exposure Time:', '%.4g sec' % float(DataNode.ExposureTime)])
    
        if 'ExposureDose' in DataNode.attrib:
            rows.append(['Exposure Dose:', '%.4g nm/sec' % float(DataNode.ExposureDose)])
    
        if 'Magnification' in DataNode.attrib:
            rows.append(['Magnification:', '%.4g X' % float(DataNode.Magnification)])
    
        if 'PixelSpacing' in DataNode.attrib:
            rows.append(['Pixel Spacing:', '%.4g' % float(DataNode.PixelSpacing)])
    
        if 'SpotSize' in DataNode.attrib:
            rows.append(['Spot Size:', '%d' % int(DataNode.SpotSize)])
    
        if 'TargetDefocus' in DataNode.attrib:
            try:
                rows.append(['Target Defocus:', '%.4g' % float(DataNode.TargetDefocus)])
            except ValueError:
                # A SerialEM upgrade in July 2020 removed the TargetDefocus attribute from each tile's idoc entry
                rows.append(['Target Defocus:', ''])
                pass
        
    except ValueError:
        nornir_shared.prettyoutput.LogErr("Could not convert IDoc Data from Element: {0}".format(DataNode.FullPath))
        pass
    
    return rows

#     ExposureList = []
#     MagList = []
#     SettingList = []
#     Columns = ColumnList()
#
#     if 'ExposureTime' in DataNode.attrib:
#         ExposureList.append(['Exposure Time:', '%.4g sec' % float(DataNode.ExposureTime)])
#
#     if 'ExposureDose' in DataNode.attrib:
#         ExposureList.append(['Exposure Dose:', '%.4g nm/sec' % float(DataNode.ExposureDose)])
#
#     if 'Magnification' in DataNode.attrib:
#         MagList.append(['Magnification:', '%.4g X' % float(DataNode.Magnification)])
#
#     if 'PixelSpacing' in DataNode.attrib:
#         MagList.append(['Pixel Spacing:', '%.4g' % float(DataNode.PixelSpacing)])
#
#     if 'SpotSize' in DataNode.attrib:
#         SettingList.append(['Spot Size:', '%d' % int(DataNode.SpotSize)])
#
#     if 'TargetDefocus' in DataNode.attrib:
#         SettingList.append(['Target Defocus:', '%.4g' % float(DataNode.TargetDefocus)])
#
#     if len(ExposureList) > 0:
#         Columns.append(ExposureList)
#
#     if len(MagList) > 0:
#         Columns.append(MagList)
#
#     if len(SettingList) > 0:
#         Columns.append(SettingList)
#
#     return Columns


def HTMLFromIDocDataNode(DataNode, htmlpaths, MaxImageWidth=None, MaxImageHeight=None, **kwargs):
    '''
    <Data CreationDate="2013-12-16 11:58:15" DataMode="6" ExposureDose="0" ExposureTime="0.5" Image="10000.tif"
     ImageSeries="1" Intensity="0.52256" Magnification="5000" Montage="1" Name="IDoc" Path="1.idoc" PixelSpacing="21.76" 
     RotationAngle="-178.3" SpotSize="3" TargetDefocus="-0.5" TiltAngle="0.1" Version="1.0" />
    '''
    
    TableEntries = {'1': htmlpaths.GetFileAnchorHTML(DataNode, "Tile data (idoc file)")}
    # rows.insert(0, htmlpaths.GetFileAnchorHTML(DataNode, "Capture Settings Summary"))
    
    SummaryStrings = __ExtractIDocDataText(DataNode)
    
    if SummaryStrings is not None and len(SummaryStrings) > 0:
        TableEntries['0'] = SummaryStrings
    
    # TODO: Plot the defocus values for the entire idoc
    idocFilePath = DataNode.FullPath
    if os.path.exists(idocFilePath):

        Data = idoc.IDoc.Load(idocFilePath)
        RelPath = htmlpaths.GetSubNodeRelativePath(DataNode)
        
        TPool = nornir_pools.GetGlobalMultithreadingPool()
        
        salt_str = GetTempFileSaltString(DataNode)
        
        DefocusImgFilename = salt_str + "Defocus.svg"
        DefocusThumbnailFilename = salt_str + "Defocus_Thumbnail.png"
        
        DefocusSettleImgSrcPath = os.path.join(htmlpaths.ThumbnailRelative, DefocusImgFilename)
        DefocusSettleThumbnailImgSrcPath = os.path.join(htmlpaths.ThumbnailRelative, DefocusThumbnailFilename)
        
        DefocusImgOutputFullPath = os.path.join(htmlpaths.ThumbnailDir, DefocusImgFilename)
        DefocusThumbnailOutputFullPath = os.path.join(htmlpaths.ThumbnailDir, DefocusThumbnailFilename)
        
        HTMLDefocusImage = HTMLImageTemplate % {'src': DefocusSettleThumbnailImgSrcPath, 'AltText': 'Defocus Image', 'ImageWidth': MaxImageWidth, 'ImageHeight': MaxImageHeight}
        HTMLDefocusAnchor = HTMLAnchorTemplate % {'href': DefocusSettleImgSrcPath, 'body': HTMLDefocusImage }
 
        TPool.add_task(DefocusThumbnailFilename, idoc.PlotDefocusSurface, idocFilePath, (DefocusThumbnailOutputFullPath, DefocusImgOutputFullPath))
        TableEntries["2"] = HTMLDefocusAnchor
        
    return TableEntries


def HTMLFromLogDataNode(DataNode, htmlpaths, MaxImageWidth=None, MaxImageHeight=None, **kwargs):

    if MaxImageWidth is None:
        MaxImageWidth = 1024

    if MaxImageHeight is None:
        MaxImageHeight = 1024

    if not DataNode.Name == 'Log':
        return None

    TableEntries = {}

    logFilePath = DataNode.FullPath
    if os.path.exists(logFilePath):

        Data = serialemlog.SerialEMLog.Load(logFilePath)

        RelPath = htmlpaths.GetSubNodeRelativePath(DataNode)

        TableEntries["2"] = __ExtractLogDataText(Data)

        TPool = nornir_pools.GetGlobalMultithreadingPool()

        LogSrcFullPath = os.path.join(RelPath, DataNode.Path)
        
        salt_str = GetTempFileSaltString(DataNode)

        DriftSettleSrcFilename = salt_str + "DriftSettle.svg"
        DriftSettleThumbnailFilename = salt_str + "DriftSettle_Thumb.png"
        DriftSettleThumbImgSrcPath = os.path.join(htmlpaths.ThumbnailRelative, DriftSettleThumbnailFilename)
        DriftSettleImgSrcPath = os.path.join(htmlpaths.ThumbnailRelative, DriftSettleSrcFilename)
        DriftSettleImgOutputFullPath = os.path.join(htmlpaths.ThumbnailDir, DriftSettleSrcFilename)
        DriftSettleThumbnailOutputFullPath = os.path.join(htmlpaths.ThumbnailDir, DriftSettleThumbnailFilename)

        # nfiles.RemoveOutdatedFile(logFilePath, DriftSettleThumbnailOutputFullPath)
        # if not os.path.exists(DriftSettleThumbnailOutputFullPath):
        TPool.add_task(DriftSettleThumbnailFilename, serialemlog.PlotDriftSettleTime, logFilePath, (DriftSettleImgOutputFullPath, DriftSettleThumbnailOutputFullPath))

        DriftGridSrcFilename = salt_str + "DriftGrid.svg"
        DriftGridThumbnailFilename = salt_str + "DriftGrid_Thumb.png"
        DriftGridThumbImgSrcPath = os.path.join(htmlpaths.ThumbnailRelative, DriftGridThumbnailFilename)
        DriftGridImgSrcPath = os.path.join(htmlpaths.ThumbnailRelative, DriftGridSrcFilename)
        DriftGridImgOutputFullPath = os.path.join(htmlpaths.ThumbnailDir, DriftGridSrcFilename)
        DriftGridThumbnailOutputFullPath = os.path.join(htmlpaths.ThumbnailDir, DriftGridThumbnailFilename)

        # nfiles.RemoveOutdatedFile(logFilePath, DriftGridThumbnailFilename)
        # if not os.path.exists(DriftGridThumbnailFilename):
        TPool.add_task(DriftGridThumbnailFilename, serialemlog.PlotDriftGrid, logFilePath, (DriftGridImgOutputFullPath, DriftGridThumbnailOutputFullPath))

        # Build a histogram of drift settings
#        x = []
#        y = []
#        for t in Data.tileData.values():
#            if not (t.dwellTime is None or t.drift is None):
#                x.append(t.dwellTime)
#                y.append(t.drift)
#
#        ThumbnailFilename = GetTempFileSaltString() + "Drift.png"
#        ImgSrcPath = os.path.join(ThumbnailDirectoryRelPath, ThumbnailFilename)
#        ThumbnailOutputFullPath = os.path.join(ThumbnailDirectory, ThumbnailFilename)

                # PlotHistogram.PolyLinePlot(lines, Title="Stage settle time, max drift %g" % maxdrift, XAxisLabel='Dwell time (sec)', YAxisLabel="Drift (nm/sec)", OutputFilename=ThumbnailOutputFullPath)
        HTMLDriftSettleImage = HTMLImageTemplate % {'src': DriftSettleThumbImgSrcPath, 'AltText': 'Drift scatterplot', 'ImageWidth': MaxImageWidth, 'ImageHeight': MaxImageHeight}
        HTMLDriftSettleAnchor = HTMLAnchorTemplate % {'href': DriftSettleImgSrcPath, 'body': HTMLDriftSettleImage }

        HTMLDriftGridImage = HTMLImageTemplate % {'src': DriftGridThumbImgSrcPath, 'AltText': 'Drift scatterplot', 'ImageWidth': MaxImageWidth, 'ImageHeight': MaxImageHeight}
        HTMLDriftGridAnchor = HTMLAnchorTemplate % {'href': DriftGridImgSrcPath, 'body': HTMLDriftGridImage }

        TableEntries["1"] = HTMLAnchorTemplate % {'href': LogSrcFullPath, 'body': "Log File" }
        TableEntries["3"] = ColumnList([HTMLDriftSettleAnchor, HTMLDriftGridAnchor])
    else:
        TableEntries = []
        
        if 'AverageTileDrift' in DataNode.attrib:
            TableEntries.append(['Average tile drift:', '%.3g nm/sec' % float(DataNode.AverageTileDrift)])

        if 'MinTileDrift' in DataNode.attrib:
            TableEntries.append(['Min tile drift:', '%.3g nm/sec' % float(DataNode.MinTileDrift)])

        if 'MaxTileDrift' in DataNode.attrib:
            TableEntries.append(['Max tile drift:', '%.3g nm/sec' % float(DataNode.MaxTileDrift)])

        if 'AverageTileTime' in DataNode.attrib:
            TableEntries.append(['Average tile time:', '%.3g' % float(DataNode.AverageTileTime)])

        if 'FastestTileTime' in DataNode.attrib:
            dtime = datetime.timedelta(seconds=float(DataNode.FastestTileTime))
            TableEntries.append(['Fastest tile time:', str(dtime)])

        if 'CaptureTime' in DataNode.attrib:
            dtime = datetime.timedelta(seconds=float(DataNode.CaptureTime))
            TableEntries.append(['Total capture time:', str(dtime)])

    if len(TableEntries) == 0:
        return None

    # HTML = MatrixToTable(TableEntries)
    return TableEntries


def AddImageToTable(TableEntries, htmlPaths, DriftSettleThumbnailFilename):
    DriftSettleThumbnailFilename = GetTempFileSaltString() + "DriftSettle.png"
    DriftSettleImgSrcPath = os.path.join(htmlPaths.ThumbnailRelative, DriftSettleThumbnailFilename)
    DriftSettleThumbnailOutputFullPath = os.path.join(htmlPaths.ThumbnailDir, DriftSettleThumbnailFilename)
    

def __ScaleImage(ImageNode, HtmlPaths, MaxImageWidth=None, MaxImageHeight=None):
    '''Scale an image to be smaller than the maximum dimensions.  Vector based images such as SVG will not be scaled
    :return: (Image_source_path, Width, Height) Image source path may refer to a copy or the original.
    '''
    Height = MaxImageHeight
    Width = MaxImageWidth
    try:
        (_, ext) = os.path.splitext(ImageNode.FullPath)
        if ext == '.svg':
            return (HtmlPaths.GetSubNodeFullPath(ImageNode), Height, Width)
        
        (Height, Width) = ImageNode.Dimensions
        # [Height, Width] = nornir_imageregistration.GetImageSize(ImageNode.FullPath)
    except IOError:
        return (HtmlPaths.GetSubNodeFullPath(ImageNode), Height, Width)

    # Create a thumbnail if needed
    if Width > MaxImageWidth or Height > MaxImageHeight:
        Scale = max(float(Width) / MaxImageWidth, float(Height) / MaxImageHeight)
        Scale = 1.0 / Scale
  
        ThumbnailFilename = GetTempFileSaltString(ImageNode) + ImageNode.Path
        ImgSrcPath = os.path.join(HtmlPaths.ThumbnailRelative, ThumbnailFilename)

        ThumbnailOutputFullPath = os.path.join(HtmlPaths.ThumbnailDir, ThumbnailFilename)

        if not os.path.exists(ThumbnailOutputFullPath) or nornir_shared.files.IsOutdated(ReferenceFilename=ImageNode.FullPath, TestFilename=ThumbnailOutputFullPath):
        # nfiles.RemoveOutdatedFile(ImageNode.FullPath, ThumbnailOutputFullPath)
        # if not os.path.exists(ThumbnailOutputFullPath):
            Pool = nornir_pools.GetGlobalThreadPool()
            Pool.add_task(ImageNode.FullPath, nornir_imageregistration.Shrink, ImageNode.FullPath, ThumbnailOutputFullPath, Scale)
        # cmd = "magick convert " + ImageNode.FullPath + " -resize " + str(Scale * 100) + "% " + ThumbnailOutputFullPath
        
        # Pool.add_process(cmd, cmd + " && exit", shell=True)

        Width = int(Width * Scale)
        Height = int(Height * Scale)
    else:
        ImgSrcPath = HtmlPaths.GetSubNodeFullPath(ImageNode)
        
    return (ImgSrcPath, Height, Width)


def HTMLFromFilterNode(filter, htmlpaths, MaxImageWidth=None, MaxImageHeight=None, **kwargs):
    '''Create the HTML to display the basic information about a filter'''
    assert(not filter is None)
    
    HTML = HTMLBuilder()
    HTML.Add("<TABLE>")
    HTML.Add('<CAPTION align="top">%s</CAPTION>' % (filter.Parent.Name + '.' + filter.Name))
        
    if MaxImageWidth is None:
        MaxImageWidth = 1024

    if MaxImageHeight is None:
        MaxImageHeight = 1024
        
    if filter.HasImageset:
        HTML.Add('<TR><TD colspan="99">')  
        HTML.Add(ImgTagFromImageSetNode(filter.Imageset, htmlpaths, MaxImageWidth, MaxImageHeight, **kwargs)) 
        HTML.Add("</TD></TR>")
        
    HTML.Add("<TR>") 
    if filter.HasTileset:
        HTML.Add('<TD align="center" bgcolor="#A0FFA0">Optimized</TD>')
    else:
        HTML.Add('<TD align="center" bgcolor="#FFA0A0">Unoptimized</TD>')
        
    if filter.Locked: 
        HTML.Add('<TD align="center" bgcolor="#8080FF">Locked</TD>')
    else:
        HTML.Add('<TD align="center"  bgcolor="#AAAAAA">Unlocked</TD>')
          
    HTML.Add("</TR>")
    
    if(not (filter.Gamma is None or filter.MinIntensityCutoff is None or filter.MaxIntensityCutoff is None)):
        HTML.Add('<TR><TD align="center">%d - %d</TD><TD align="center">Gamma %g</TD></TR>' % (filter.MinIntensityCutoff, filter.MaxIntensityCutoff, filter.Gamma))    
    
    HTML.Add("</TABLE>")
    
    return str(HTML)

def ImgTagFromImageSetNode(Imageset, HtmlPaths, MaxImageWidth=None, MaxImageHeight=None, Logger=None, **kwargs):
    '''
    Returns an image tag with an anchor link to the highest resolution image.  Generates a thumbnail matching the Max Width/Height limits using
    the closest downsample level from the ImageSet for speed.
    '''
    
    if Imageset is None:
        raise ValueError("Imageset is None")
    if Logger is None:
        raise ValueError("Logger is None")
    
    requiredLevel = Imageset.FindDownsampleForSize((MaxImageHeight, MaxImageWidth))
    if requiredLevel is None:
        return ""
    
    maxResImageNode = Imageset.GetImage(Imageset.MaxResLevel.Downsample)
    AnchorHREF = HtmlPaths.GetSubNodeFullPath(maxResImageNode)
    image_node = Imageset.GetImage(requiredLevel)
    return ImgTagFromImageNode(image_node, HtmlPaths, AnchorHREF=AnchorHREF, MaxImageWidth=MaxImageWidth, MaxImageHeight=MaxImageHeight, Logger=Logger, **kwargs) 
     

def ImgTagFromImageNode(ImageNode, HtmlPaths, AnchorHREF=None, MaxImageWidth=None, MaxImageHeight=None, Logger=None, **kwargs):
    '''Create the HTML to display an image with an anchor to the full image.
       If specified RelPath should be added to the elements path for references in HTML instead of using the fullpath attribute'''

    assert(not ImageNode is None)
    assert(not Logger is None)
    if MaxImageWidth is None:
        MaxImageWidth = 1024

    if MaxImageHeight is None:
        MaxImageHeight = 1024

    imageFilename = ImageNode.Path
 
    if not os.path.exists(ImageNode.FullPath):
        Logger.error("Missing image file: " + ImageNode.FullPath)
        return ""

    (ImgSrcPath, Height, Width) = __ScaleImage(ImageNode, HtmlPaths, MaxImageWidth, MaxImageHeight)
    
    if AnchorHREF is None:
        AnchorHREF = HtmlPaths.GetSubNodeFullPath(ImageNode)

    HTMLImage = HTMLImageTemplate % {'src': ImgSrcPath, 'AltText': imageFilename, 'ImageWidth': Width, 'ImageHeight': Height}
    HTMLAnchor = HTMLAnchorTemplate % {'href': AnchorHREF, 'body': HTMLImage }

    return HTMLAnchor


def __anchorStringForHeader(Text):
    return '<a id="%(id)s"><b>%(id)s</b></a>' % {'id': Text}


def HTMLFromTransformNode(ColSubElement, HtmlPaths, **kwargs):
    return '<a href="%s">%s</a>' % (HtmlPaths.GetSubNodeFullPath(ColSubElement), ColSubElement.Name)


def RowReport(RowElement, HTMLPaths, RowLabelAttrib=None, ColumnXPaths=None, Logger=None, **kwargs):
    '''Create HTML to describe an element'''
    if Logger is None:
        Logger = logging.getLogger(__name__ + ".RowReport")

    if not isinstance(ColumnXPaths, list):
        xpathStrings = str(ColumnXPaths).strip().split(',')
        ColumnXPaths = xpathStrings

    if len(ColumnXPaths) == 0:
        return

    ColumnBodyList = ColumnList()

    if hasattr(RowElement, RowLabelAttrib):
        RowLabel = str(getattr(RowElement, RowLabelAttrib))

    if RowLabel is None:
        RowLabel = str(RowElement)

    # OK, build the columns
    astr = __anchorStringForHeader(RowLabel)
    ColumnBodyList.append(astr)

    ArgSet = ArgumentSet()

    ArgSet.AddArguments(kwargs)
    
    BobEndpoint = None
    
    if RowElement.tag == 'Section' and BobEndpoint is not None:
        AddBobButtons(BobEndpoint=BobEndpoint)
        
    # CaptionHTML = None
    for ColXPath in ColumnXPaths:

        # ColXPath = ArgSet.SubstituteStringVariables(ColXPath)
        ColSubElements = RowElement.findall(ColXPath)
        # Create a new table inside if len(ColSubElements) > 1?
        for ColSubElement in ColSubElements:

            HTML = None
            if ColSubElement.tag == "Tileset":
                ColumnBodyList.bgColor = '#A0FFA0'
            elif ColSubElement.tag == "Filter":
                HTML = HTMLFromFilterNode(filter=ColSubElement, htmlpaths=HTMLPaths, MaxImageWidth=364, MaxImageHeight=364, Logger=Logger)
            elif ColSubElement.tag == "Image":
                if ColSubElement.FindParent("ImageSet") is None:
                    kwargs['MaxImageWidth'] = 364
                    kwargs['MaxImageHeight'] = 364
                else:
                    kwargs['MaxImageWidth'] = 448
                    kwargs['MaxImageHeight'] = 448

                HTML = ImgTagFromImageNode(ImageNode=ColSubElement, HtmlPaths=HTMLPaths, Logger=Logger, **kwargs)
            elif ColSubElement.tag == "TransformData":
                HTML = ImgTagFromImageNode(ImageNode=ColSubElement, HtmlPaths=HTMLPaths, Logger=Logger, **kwargs)
            elif ColSubElement.tag == "Data":
                kwargs['MaxImageWidth'] = 364
                kwargs['MaxImageHeight'] = 364

                HTML = HTMLFromDataNode(ColSubElement, HTMLPaths, Logger=Logger, **kwargs)

            elif ColSubElement.tag == "Transform":
                HTML = HTMLFromTransformNode(ColSubElement, HTMLPaths, Logger=Logger, **kwargs)

            elif ColSubElement.tag == "Notes":
                ColumnBodyList.caption = '<caption align=bottom class="notes">%s</caption>\n' % HTMLFromNotesNode(ColSubElement, HTMLPaths, Logger=Logger, **kwargs)
            
            if not HTML is None:
                ColumnBodyList.append(HTML)

    # if not CaptionHTML is None:
    #   ColumnBodyList.caption = '<caption align=bottom>%s</caption>' % CaptionHTML
    
    return ColumnBodyList


def AddBobButtons(BobEndpoint):
    raise NornirUserException('Not Implemented')
    # Do a python get for Bob server data in javascript
    #
    # {
    #    var IsMerged = jscript.CallAWebsite(BobEndpoint)
    #    if(
    # }
    #
            
    # HTML = "<A HREF={BobEndpoint}/Merge/>Merge</A>


def GenerateTableReport(OutputFile, ReportingElement, RowXPath, RowLabelAttrib=None, ColumnXPaths=None, RowsPerPage=None, BuilderEndpoint=None, Logger=None, **kwargs):
    '''Create an HTML table that uses the RowXPath as the root for searches listed under ColumnXPaths
       ColumnXPaths are a list of comma delimited XPath searches.  Each XPath search results in a new column for the row
       Much more sophisticated reports would be possible by building a framework similiar to the pipeline manager, but time'''

    if BuilderEndpoint is not None:
        if not validators.url(BuilderEndpoint):
            raise NornirUserException(f"Invalid BuilderEndpoint: {BuilderEndpoint}")
        
    if RowsPerPage is None:
        RowsPerPage = 50
        
    if RowLabelAttrib is None:
        RowLabelAttrib = "Name"
        
    if not OutputFile.endswith('.html'):
        OutputFile += '.html'

    if not isinstance(ColumnXPaths, list):
        xpathStrings = str(ColumnXPaths).strip().split(',')
        ColumnXPaths = xpathStrings

    if len(ColumnXPaths) == 0:
        return

    RootElement = ReportingElement
    while hasattr(RootElement, 'Parent'):
        if not RootElement.Parent is None:
            RootElement = RootElement.Parent
        else:
            break

    Paths = HTMLPaths(RootElement.FullPath, OutputFile)

    Paths.CreateOutputDirs()

    # OK, start walking the columns.  Then walk the rows
    RowElements = ReportingElement.findall(RowXPath)

    # Build a 2D list to build the table from later

    # pool = nornir_pools.GetGlobalThreadPool()
    tableDict = {}
    tasks = []

    NumRows = 0
    for (iRow, RowElement) in enumerate(RowElements):
        NumRows += 1
        if hasattr(RowElement, RowLabelAttrib):
            RowLabel = getattr(RowElement, RowLabelAttrib)

        if RowLabel is None:
            RowLabel = RowElement

        # task = pool.add_task(RowLabel, RowReport, RowElement, RowLabelAttrib=RowLabelAttrib, ColumnXPaths=ColumnXPaths, HTMLPaths=Paths, Logger=Logger, **kwargs)
        # tasks.append(task)
        # task.wait()

        # Threading this caused problems with Matplotlib being called from different threads.  Single threading again for now
        result = RowReport(RowElement, RowLabelAttrib=RowLabelAttrib, ColumnXPaths=ColumnXPaths, HTMLPaths=Paths, Logger=Logger, **kwargs)
        tableDict[RowLabel] = result

    if NumRows == 0:
        return

    for iRow, t in enumerate(tasks):
        try:
            tableDict[t.name] = t.wait_return()
            nornir_shared.prettyoutput.CurseProgress("Added row", iRow, Total=NumRows)
        except Exception as e:
            tableDict[t.name] = str(e)
            pass

    # HTML = MatrixToTable(RowBodyList=RowBodyList)
    Pages = DictToPages(tableDict, Paths, RowsPerPage)
    
    for iPage in range(0, len(Pages)):
        CreateHTMLDoc(os.path.join(Paths.OutputDir, Paths.OutputPage(iPage)), HTMLBody=Pages[iPage])
    # HTML = DictToTable(tableDict) #paginate here
     
    # CreateHTMLDoc(os.path.join(Paths.OutputDir, Paths.OutputFile), HTMLBody=HTML)
    return


def DictToPages(RowDict, Paths, RowsPerPage, IndentLevel=0):
    ''':return: A list of HTMLBuilder objects, each describing a page'''
    pages = []
    
    if RowDict is None:
        raise Exception('Missing RowDict')
    
    if Paths is None:
        raise Exception('Missing Paths')
    
    keys = list(RowDict.keys())
    
    if len(keys) < RowsPerPage:
        pages.append(DictToTable(RowDict))
        return pages
    
    NumPages = math.ceil(len(keys) / RowsPerPage)
    if NumPages < 2:
        HTML = DictToTable(RowDict)  # paginate here
        pages.add(HTML)
        return
    
    keys.sort(reverse=True)
    
    for iPage in range(0, NumPages): 
        HTML = HTMLBuilder(IndentLevel)
        HTML.Add('<div class="navbar">\n')
        HTML.Indent()
        AddPageNavigation(HTML, Paths, keys, NumPages, iPage, RowsPerPage)
        HTML.Dedent()
        HTML.Add('</div>\n')
        
        HTML.Add('<div class="main">\n')
        HTML.Indent()
        DictToPage(HTML, RowDict, Paths, keys, iPage, RowsPerPage)
        HTML.Dedent()
        HTML.Add('</div>\n')        
        
        pages.append(HTML)
         
    return pages


def __getKeyIndiciesForPage(iPage, RowsPerPage):
    iStartKey = iPage * RowsPerPage
    iEndKey = (iPage + 1) * RowsPerPage
    return (iStartKey, iEndKey)


def __getKeysForPage(SortedKeys, iPage, RowsPerPage):
    (iStartKey, iEndKey) = __getKeyIndiciesForPage(iPage, RowsPerPage)
    return SortedKeys[iStartKey:iEndKey]


def __getFirstAndLastKeysForPage(SortedKeys, iPage, RowsPerPage):
    subset = __getKeysForPage(SortedKeys, iPage, RowsPerPage)
    return (subset[0], subset[-1])


def DictToPage(HTML, RowDict, Paths, SortedKeys, iPage=0, RowsPerPage=50, IndentLevel=0):
    '''
    Create a set of pages with next/prev links to adjacent pages
    '''
    
    if SortedKeys is None:
        raise Exception('Missing SortedKeys') 
    
    # HTML = HTMLBuilder(IndentLevel)
    
    if IndentLevel is None:
        HTML.Add('<table border="border">\n')
    else:
        HTML.Add("<table>\n")
    HTML.Indent() 
     
    for row in __getKeysForPage(SortedKeys, iPage, RowsPerPage):
        HTML.Add(__ValueToTableRow(RowDict[row], HTML.IndentLevel))
        
    if hasattr(RowDict, 'caption'):
        HTML.Add(RowDict.caption)
        
    HTML.Dedent()
    HTML.Add("</table>\n")
         
    return HTML
     
        
def AddPageNavigation(HTML, Paths, SortedKeys, NumPages, iPage=0, RowsPerPage=50,):
    '''Add next/prev buttons and a button for every page number'''
    if iPage < NumPages:
        pageRange = __getFirstAndLastKeysForPage(SortedKeys, iPage, RowsPerPage)
        nextPage = HTMLAnchorTemplate % {'href': Paths.OutputPage(iPage + 1), 'body': f'Next {pageRange[0]} - {pageRange[1]}&nbsp;' }
        HTML.Add(nextPage)
        
    if iPage > 0:
        pageRange = __getFirstAndLastKeysForPage(SortedKeys, iPage, RowsPerPage)
        prevPage = HTMLAnchorTemplate % {'href': Paths.OutputPage(iPage - 1), 'body': f'&nbsp;Prev {pageRange[0]} - {pageRange[1]}' }
        HTML.Add(prevPage)
        
    for pagenum in range(0, NumPages): 
        
        pageRange = __getFirstAndLastKeysForPage(SortedKeys, pagenum, RowsPerPage)
        if pagenum == iPage:
            HTML.Add('<div class="selected">\n')
            HTML.Add(f'<p>{pageRange[0]} - {pageRange[1]}</p>')
            HTML.Add('</div>\n')
        else:
            pageAnchor = HTMLAnchorTemplate % {'href': Paths.OutputPage(pagenum), 'body': f'&nbsp;{pageRange[0]} - {pageRange[1]}&nbsp;' }
            HTML.Add(pageAnchor)
            
            
        
        
    # TODO: Add a button for every page
 
    return

     
def DictToTable(RowDict, IndentLevel=None):
    
    if RowDict is None:
        raise Exception('Missing RowDict')

    HTML = HTMLBuilder(IndentLevel)

    if IndentLevel is None:
        HTML.Add('<table border="border">\n')
    else:
        HTML.Add("<table>\n")
    HTML.Indent()

    keys = list(RowDict.keys())
    keys.sort(reverse=True)

    for row in keys: 
        HTML.Add(__ValueToTableRow(RowDict[row], HTML.IndentLevel))

    if hasattr(RowDict, 'caption'):
        HTML.Add(RowDict.caption)

    HTML.Dedent()
    HTML.Add("</table>\n")

    return HTML


def CreateHTMLDoc(OutputFile, HTMLBody):
    HTMLHeader = HTMLBuilder()
    HTMLHeader.Add("<!DOCTYPE html>")
    HTMLHeader.Add("<html>\n")
    HTMLHeader.Indent()
    HTMLHeader.Add("<header>\n")
    HTMLHeader.Indent()
    HTMLHeader.Add("<style>\n")
    HTMLHeader.Indent()
    HTMLHeader.Add("""
                        body {margin:0;
                        font-size:16px
                        }
                        .navbar {
                          overflow: hidden;
                          background-color: #333;
                          position: fixed;
                          top: 0;
                          width: 100%;
                        }
                        .navbar a {
                          float: left;
                          display: block;
                          color: #f2f2f2;
                          text-align: center;
                          padding: 14px 16px;
                          text-decoration: none;
                          font-size: 17px;
                        }
                        .navbar p {
                          float: left;
                          display: block;
                          color: black;
                          background: #ddd;
                          text-align: center;
                          padding: 14px 16px;
                          text-decoration: none;
                          font-size: 20px;
                        }
                        
                        .navbar a:hover {
                          background: #ddd;
                          color: black;
                        }
                        .main {
                          padding: 1px;
                          margin-top: 100px;
                        }
                        
                        .main.selected {
                          background: #ddd;
                          color: black;
                        }
                        
                        .notes {
                          background: #E0E0E0;
                        }
                        
                        .logheader { 
                          font-size:18px;
                        }
                        """)
    HTMLHeader.Dedent()
    HTMLHeader.Add("</style>\n")
    HTMLHeader.Dedent()
    HTMLHeader.Add("</header>\n")
    HTMLHeader.Add("<body>\n")
    HTMLHeader.Indent()
    HTMLHeader.Add(HTMLBody)
    HTMLHeader.Dedent()
    HTMLHeader.Add("</body>\n")
    HTMLHeader.Dedent()
    HTMLHeader.Add("</html>\n")

    HTML = str(HTMLHeader)

    if os.path.exists(OutputFile):
        os.remove(OutputFile)

    if not OutputFile is None:
        f = open(OutputFile, 'w')
        f.write(HTML)
        f.close()


def __IndentString(IndentLevel):
    return ' ' * IndentLevel


def __AppendHTML(html, newHtml, IndentLevel):
    html.append(__IndentString(IndentLevel) + newHtml)

    
def __ValueToTableRow(value, IndentLevel):
    HTML = HTMLBuilder(IndentLevel)
    
    if hasattr(value, 'bgColor'):
        bgColor = value.bgColor
        HTML.Add('<tr bgcolor="%s">\n' % bgColor)
    else:
        HTML.Add('<tr>')

    HTML.Indent()
    
    HTML.Add(__ValueToTableCell(value, HTML.IndentLevel))

    HTML.Dedent()
    HTML.Add("</tr>\n")
    
    return HTML


def __ValueToTableCell(value, IndentLevel):
    '''Converts a value to a table cell'''
    HTML = HTMLBuilder(IndentLevel)
    if hasattr(value, 'bgColor'):
        bgColor = value.bgColor
        HTML.Add('<td bgcolor="%s" valign="top">\n' % bgColor)
    else:
        HTML.Add('<td valign="top">')
    
    if isinstance(value, str):
        HTML.Add(value)
    elif isinstance(value, dict):
        HTML.Indent()
        HTML.Add(DictToTable(value, HTML.IndentLevel))
        HTML.Dedent()
    elif isinstance(value, UnorderedItemList):
        HTML.Indent()
        HTML.Add(__ListToUnorderedList(value, HTML.IndentLevel))
        HTML.Dedent()
    elif isinstance(value, RowList):
        HTML.Indent()
        HTML.Add(__ListToTableRows(value, HTML.IndentLevel))
        HTML.Dedent()
    elif isinstance(value, ColumnList):
        HTML.Indent()
        HTML.Add(__ListToTableColumns(value, HTML.IndentLevel))
        HTML.Dedent()
    elif isinstance(value, list):
        HTML.Indent()
        HTML.Add(__ListToTableColumns(value, HTML.IndentLevel))
        HTML.Dedent()
    else:
        HTML.Add(f"Unknown type passed to __ValueToHTML: {value}")

    HTML.Add("</td>\n")

    return HTML


def __ListToTableColumns(listColumns, IndentLevel):
    '''Convert a list to a set of <tf> columns in a table'''

    HTML = HTMLBuilder(IndentLevel)

    HTML.Add("<table>\n")
    HTML.Indent()
    HTML.Add("<tr>\n")
    HTML.Indent()

    for entry in listColumns:
        HTML.Add(__ValueToTableCell(entry, HTML.IndentLevel))

    HTML.Dedent()
    HTML.Add("</tr>\n")

    if hasattr(listColumns, 'caption'):
        HTML.Add(listColumns.caption)

    HTML.Dedent()
    HTML.Add("</table>\n")

    return HTML


def __ListToTableRows(listColumns, IndentLevel):
    '''Convert a list to a set of <tf> columns in a table'''

    HTML = HTMLBuilder(IndentLevel)

    HTML.Add("<table>\n")
    HTML.Indent()

    for entry in listColumns:
        HTML.Add("<tr>\n")
        HTML.Indent()

        HTML.Add(__ValueToTableCell(entry, HTML.IndentLevel))

        HTML.Dedent()
        HTML.Add("</tr>\n")

    if hasattr(listColumns, 'caption'):
        HTML.Add(listColumns.caption)

    HTML.Dedent()
    HTML.Add("</table>\n")

    return HTML


def __ListToUnorderedList(listEntries, IndentLevel):
    '''Convert a list to a set of <tf> columns in a table'''

    HTML = HTMLBuilder(IndentLevel)

    HTML.Add("<ul>\n")
    HTML.Indent()

    for entry in listEntries:
        HTML.Add(' ' * HTML.IndentLevel + '<li>' + str(entry) + '</li>\n')

    HTML.Dedent()
    HTML.Add("</ul>\n")

    return HTML


def MatrixToTable(RowBodyList=None, IndentLevel=None):
    '''Convert a list of lists containing HTML fragments into a table'''

    if IndentLevel is None:
        IndentLevel = 0

    HTML = ' ' * IndentLevel + "<table>\n"

    for columnList in RowBodyList:
        HTML = HTML + ' ' * IndentLevel + '<tr>\n'

        IndentLevel = IndentLevel + 1

        if isinstance(columnList, str):
            HTML = HTML + '<td>'
            HTML = HTML + columnList
            HTML = HTML + "</td>\n"
        else:
            FirstColumn = True
            for column in columnList:
                HTML = HTML + ' ' * IndentLevel
                if FirstColumn:
                    HTML = HTML + '<td valign="top">'
                    FirstColumn = False
                else:
                    HTML = HTML + '<td align="left">'

                HTML = HTML + column
                HTML = HTML + "</td>\n"

        IndentLevel = IndentLevel - 1
        HTML = HTML + ' ' * IndentLevel + "</tr>\n"

    HTML = HTML + ' ' * IndentLevel + "</table>\n"

    return HTML


def GenerateImageReport(xpaths, VolumeElement, Logger, OutputFile=None, **kwargs):

    if(OutputFile is None):
        OutputFile = os.path.join(VolumeElement.FullPath, 'Report.html')

    if isinstance(xpaths, str):
        xpaths = [xpaths]

    if not isinstance(xpaths, list):
        xpathStrings = str(xpaths).strip().split(',')
        requiredFiles = list()
        for fileStr in  xpathStrings:
            requiredFiles.append(fileStr)

    # OK, build a tree recursively composed of matches to the xpath strings
    Dictionary = dict()
    HTMLString = RecursiveReportGenerator(VolumeElement, xpaths, Logger)

    print(HTMLString)


def RecursiveReportGenerator(VolumeElement, xpaths, Logger=None):
    List = []
    for xpath in xpaths:
        for element in VolumeElement.findall(xpath):
            if not hasattr(element, 'FullPath'):
                Logger.warning('No fullpath property on element: ' + str(element))
                continue

            Name = None
            if hasattr(element, 'Name'):
                Name = element.Name
            else:
                Name = element.GetAttribFromParent('Name')

            if Name is None:
                Logger.warning('No name property on element: ' + str(element))
                continue

            List.append((element.tag, Name, element.FullPath, element))

    for element in VolumeElement:
        childList = RecursiveReportGenerator(element, xpaths, Logger.getChild('element.Name'))
        if(len(childList) > 0):
            List.append((element.tag, element.Name, childList))

    return List
