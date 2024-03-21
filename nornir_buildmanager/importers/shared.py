'''
Created on Apr 18, 2019

@author: u0490822
'''

import glob
import os
import re
import shutil
import sys
import datetime
from typing import Iterable, NamedTuple, Callable

import nornir_buildmanager
from nornir_buildmanager.exceptions import NornirUserException
import nornir_shared.files as files
import nornir_shared.prettyoutput as prettyoutput
from nornir_shared.histogram import Histogram
import nornir_shared.plot as plot


class FilenameMetadata(NamedTuple):
    fullpath: str
    number: int
    version: str
    name: str
    downsample: int
    extension: str


class MinMaxGamma(NamedTuple):
    min: float
    max: float
    gamma: float = 1.0


class ContrastValue(NamedTuple):
    Section: int
    Min: int
    Max: int
    Gamma: int = 1.0


# FilenameMetadata = collections.namedtuple('SectionInfo', 'fullpath number version name downsample extension')
# MinMaxGamma = collections.namedtuple('MinMaxGamma', 'min max gamma')

# Global instance of our parser for filenames that is initialized upon first use
_InputFileRegExParser = None


def GetSectionInfo(fullpath) -> FilenameMetadata:
    '''Given a path or filename returns the meta data we can determine from the name
       :returns: A named tuple with (fullpath number version name downsample extension)
    '''
    fileName = os.path.basename(fullpath)

    d = ParseMetadataFromFilename(fileName)

    return FilenameMetadata(fullpath, d['Number'], d['Version'], d['Name'], d['Downsample'], d['Extension'])


def FileMetaDataStrHeader():
    output = "{0:<22}\t{1:<6}{2:<5}{3:<16}{4:<5}{5}\n".format("Path", "#", "Ver", "Name", "Ds", "ext")
    return output


def FileMetaDataStr(data):
    '''Provides a pretty string for a FilenameMetadata tuple'''
    v = data.version
    if v == '\0':
        v = None
    output = "{0:<22}\t{1:<6}{2:<5}{3:<16}{4:<5}{5}".format(os.path.basename(data.fullpath), str(data.number), str(v),
                                                            str(data.name), str(data.downsample), str(data.extension))
    return output


def _TryCleanDataWithNotInCurrentImport(input_path: str,
                                        elements: Iterable[nornir_buildmanager.volumemanager.XElementWrapper],
                                        new_section_info: FilenameMetadata | None = None) -> str:
    removed = False
    for elem in elements:
        try:
            old_section_info = GetSectionInfo(elem.Path)
        except NornirUserException:
            continue  # Do not remove information that doesn't have a parsable path

        if new_section_info is None:
            new_section_info = GetSectionInfo(input_path)

        if new_section_info.number != old_section_info.number:
            continue

        elem_file_path = os.path.join(input_path, elem.Path)
        if not os.path.exists(elem_file_path):
            elem.Clean(
                f"Removing <{elem.tag}> element created {elem.CreationTime}.  Source file not found in current import folder {elem_file_path}")
            removed = True

    return removed


def TryCleanNotes(containerObj, input_path: str, logger, new_section_info: FilenameMetadata | None = None) -> bool:
    """
    Remove notes elements whose files do not exist in the input path
    :param new_section_info: Section information for the section we are importing notes from
    :return: True if a Note element was removed
    """

    notes = containerObj.findall('Notes')
    return _TryCleanDataWithNotInCurrentImport(input_path, notes, new_section_info)


def TryCleanIdocCaptureData(containerObj, input_path: str, logger,
                            new_section_info: FilenameMetadata | None = None) -> bool:
    """
    Remove Data elements whose files do not exist in the input path
    :param new_section_info: Section information for the section we are importing notes from
    :return: True if an element was removed
    """
    data_elements = containerObj.findall('Data')
    filtered_list = []
    for data in data_elements:
        _, ext = os.path.splitext(data.Path)
        if ext == '.log' or ext == '.idoc':
            filtered_list.append(data)

    return _TryCleanDataWithNotInCurrentImport(input_path, filtered_list, new_section_info)


def TryAddHistogram(containerObj: nornir_buildmanager.volumemanager.XElementWrapper,
                    InputPath: str,
                    image_ext: str | None = None,
                    min_cutoff=None,
                    max_cutoff=None,
                    gamma=None):
    """
    :param containerObj:
    :param InputPath:
    :param logger:
    :return:
    """

    # if new_section_info is None:
    #    new_section_info = GetSectionInfo(InputPath)

    if image_ext is None:
        image_ext = '.png'

    histogram_node = nornir_buildmanager.volumemanager.HistogramNode.Create(Type='RawDataHistogram')
    [added, histogram_node] = containerObj.UpdateOrAddChildByAttrib(histogram_node, 'Type')

    histogram_image_path = os.path.join(InputPath, f'Histogram{image_ext}')
    if os.path.exists(histogram_image_path):
        image_node = nornir_buildmanager.volumemanager.ImageNode.Create(Path=f'RawDataHistogram{image_ext}',
                                                                        attrib={'Name': 'RawDataHistogram'})
        [image_added, image_node] = histogram_node.UpdateOrAddChildByAttrib(image_node, 'Name')
        existing_removed = files.RemoveOutdatedFile(histogram_image_path, image_node.FullPath)
        if image_added or existing_removed:
            shutil.copyfile(histogram_image_path, image_node.FullPath)

    histogram_xml_path = os.path.join(InputPath, 'Histogram.xml')
    if os.path.exists(histogram_xml_path):
        image_node = nornir_buildmanager.volumemanager.DataNode.Create(Path='RawDataHistogram.xml',
                                                                       attrib={'Name': 'RawDataHistogram'})
        [data_added, image_node] = histogram_node.UpdateOrAddChildByAttrib(image_node, 'Name')
        existing_removed = files.RemoveOutdatedFile(histogram_image_path, image_node.FullPath)
        if data_added or existing_removed:
            shutil.copyfile(histogram_image_path, image_node.FullPath)

    autolevel_hint = histogram_node.GetOrCreateAutoLevelHint()
    autolevel_hint.UserRequestedGamma = gamma
    autolevel_hint.UserRequestedMaxIntensityCutoff = max_cutoff
    autolevel_hint.UserRequestedMinIntensityCutoff = min_cutoff

    return added or data_added or image_added or autolevel_hint.AttributesChanged or histogram_node.ChildrenChanged


def TryAddNotes(containerObj, InputPath: str, logger, new_section_info: FilenameMetadata | None = None):
    '''
    Check the path for a notes.txt file.  If found, add a <Notes> element to the passed containerObj
    :param new_section_info: Section information for the section we are importing notes from
    '''

    if new_section_info is None:
        new_section_info = GetSectionInfo(InputPath)

    NotesFiles = glob.iglob(os.path.join(InputPath, '*.txt'))
    NotesAdded = False
    for filename in NotesFiles:

        if os.path.basename(filename) == 'ContrastOverrides.txt':
            continue

        if os.path.basename(filename) == 'Timing.txt':
            continue

        try:
            from xml.sax.saxutils import escape

            NotesFilename = os.path.basename(filename)
            CopiedNotesFullPath = os.path.join(containerObj.FullPath, NotesFilename)
            if not os.path.exists(CopiedNotesFullPath):
                os.makedirs(containerObj.FullPath, exist_ok=True)
                shutil.copyfile(filename, CopiedNotesFullPath)
                NotesAdded = True

            with open(filename, 'r') as f:
                notesTxt = f.read()
                (base, ext) = os.path.splitext(filename)
                encoding = "utf-8"
                ext = ext.lower()
                # notesTxt = notesTxt.encode(encoding)

                notesTxt = notesTxt.replace('\0', '')

                if len(notesTxt) > 0:
                    # XMLnotesTxt = notesTxt
                    # notesTxt = notesTxt.encode('utf-8')
                    XMLnotesTxt = escape(notesTxt)

                    # Create a Notes node to save the notes into
                    NotesNodeObj = nornir_buildmanager.volumemanager.NotesNode.Create(Text=XMLnotesTxt,
                                                                                      SourceFilename=NotesFilename)
                    containerObj.RemoveOldChildrenByAttrib('Notes', 'Path', NotesFilename)
                    [added, NotesNodeObj] = containerObj.UpdateOrAddChildByAttrib(NotesNodeObj, 'SourceFilename')

                    if added:
                        # Try to copy the notes to the output dir if we created a node
                        if not os.path.exists(CopiedNotesFullPath):
                            shutil.copyfile(filename, CopiedNotesFullPath)

                    NotesNodeObj.text = XMLnotesTxt
                    NotesNodeObj.encoding = encoding

                    NotesAdded = NotesAdded or added

        except:
            (etype, evalue, etraceback) = sys.exc_info()
            prettyoutput.Log("Attempt to include notes from " + filename + " failed.\n" + evalue.message)
            prettyoutput.Log(etraceback)

    return NotesAdded


def ParseMetadataFromFilename(string: str):
    '''
    Parses the filename of an input file to determine
        Number : Section Number
        Version : A letter indicating whether this is a recapture of the same section. In increasing alphabetical order.  'B' would be a recapture of 'A'
         
    '''
    global _InputFileRegExParser
    if _InputFileRegExParser is None:
        _InputFileRegExParser = re.compile(r"""
            (?P<Number>\d+)                            #Section Number
            #(?P<VersionSpace>\s)?                     #Possible space between section number and version
            (
                (?P<VersionSpace>[\s|_]+)?            #Possible space between section number and version
                (?P<Version>[^_|^\s]((?=[_|\s|\.])|$))
            )?                                         #Version letter 
            (
              (?P<DetailsSpace>[_|\s]+)                #Divider between section number/version and name, always present
              (?P<Name>(
                [a-zA-Z0-9]                            #Any letters
                |
                [ ](?![0-9]+\.)
              )+)                                      #Any spaces not followed by numbers and a period (The downsample value) 
            )?                                         #Name
            (
              (?P<DownsampleSpace>[_|\s]+)            #Divider between name and downsample/extension
              (?P<Downsample>\d+)                     #Downsample level if present
            )?
            #)                                             #Match the end of string if NumberOnly is not defined 
            (?P<Extension>\.\w+)?                          #Extension if present
            
            """, re.VERBOSE)

    m = _InputFileRegExParser.match(string)
    raiseException = m is None
    if m is not None:

        d = m.groupdict()
        section_number = d.get('Number', None)
        if section_number is not None:
            d['Number'] = int(section_number)
        else:
            raiseException = True

        version = d.get('Version', None)
        if version is None:
            version = '\0'  # Assign a letter that will sort earlier than 'A' in case someone names the first recapture A instead of B...
            d['Version'] = str.upper(version)
        else:
            d['Version'] = str.upper(str.strip(d['Version']))

        ds = d.get('Downsample', None)
        if ds is not None:
            d['Downsample'] = int(ds)

        if not raiseException:
            return d

    if raiseException:
        friendlyFormatDescription = "{Section#}[VersionLetter][_Section Name][_Downsample]\n\t{} => Required\t[] => Optional"
        raise NornirUserException(
            f'\n"{string}" cannot be parsed.\nFile/Directory meta-data is expected to be in the format:\n\t{friendlyFormatDescription}')


def CleanOutliersFromHistogram(hObj: Histogram) -> Histogram:
    """
    For Max-Value outliers this is a legacy function that supports old versions of SerialEM that falsely reported
    maxint for some pixels even though the camera was a 14-bit camera.  This applies to the original RC1 data.
    By the time RC2 was collected in March 2018 this bug was fixed

    However this function is worth retaining because Max and Min outliers can rarely occur if a tile is removed
    from the input before import but remain in the iDoc data.
    """

    hNew = Histogram.TryRemoveMaxValueOutlier(hObj, TrimOnly=False)
    if hNew is not None:
        hObj = hNew

    hNew = Histogram.TryRemoveMinValueOutlier(hObj, TrimOnly=False)
    if hNew is not None:
        hObj = hNew

    return hObj


def replace_extension(filename: str, new_extension: str) -> str:
    """
    Replace the extension of a file with a new extension
    :param filename: Filename to change
    :param new_extension: New extension to use
    :return: Filename with the new extension
    """
    (base, _) = os.path.splitext(filename)
    return f"{base}.{new_extension}"


def PlotHistogram(histogramFullPath: str, sectionNumber: int, minCutoff: float, maxCutoff: float,
                  force_recreate: bool):
    """
    :param histogramFullPath:  Output path of the image
    :param sectionNumber:
    :param minCutoff:
    :param maxCutoff:
    :param force_recreate:  If true recreate the histogram even if it exists
    :return:
    """
    HistogramImageFullPath = replace_extension(histogramFullPath, "png")
    ImageRemoved = files.RemoveOutdatedFile(histogramFullPath, HistogramImageFullPath)

    if ImageRemoved or force_recreate or not os.path.exists(HistogramImageFullPath) or files.IsOlderThan(
            HistogramImageFullPath, datetime.date(year=2022, month=10, day=25)):
        #        pool = nornir_pools.GetGlobalMultithreadingPool()
        # pool.add_task(HistogramImageFullPath, plot.Histogram, histogramFullPath, HistogramImageFullPath, Title="Section %d\nRaw Data Pixel Intensity" % (sectionNumber), LinePosList=[minCutoff, maxCutoff])
        plot.Histogram(histogramFullPath, HistogramImageFullPath,
                       Title=f"Section {sectionNumber}\nRaw Data Pixel Intensity", LinePosList=[minCutoff, maxCutoff],
                       range_is_power_of_two=True)


def _GetMinMaxCutoffs(calculate_histogram: Callable[[], Histogram],
                      min_cutoff: float,
                      max_cutoff: float,
                      histogram_cache_path: str | None = None):
    """

    :param calculate_histogram: A function which calculates a composite histogram of the section images.  Assumed to be a costly function to call.
    :param min_cutoff:
    :param max_cutoff:
    :param histogram_cache_path:
    :return:
    """
    histogramObj = None
    if histogram_cache_path is not None:
        histogramObj = Histogram.Load(histogram_cache_path)

    if histogramObj is None:
        histogramObj = calculate_histogram()

        if histogram_cache_path is not None:
            histogramObj = CleanOutliersFromHistogram(histogramObj)
            histogramObj.Save(histogram_cache_path)

    assert (histogramObj is not None)

    # I am willing to clip 1 pixel every hundred thousand on the dark side, and one every ten thousand on the light
    return histogramObj.AutoLevel(min_cutoff, max_cutoff)


def GetSectionContrastSettings(section_number: int,
                               contrast_map: dict[int, ContrastValue],
                               contrast_cutoffs: tuple[float, float],
                               calculate_histogram: Callable[[], Histogram],
                               histogram_cache_path: str) -> MinMaxGamma:
    """Clear and recreate the filters tile pyramid node if the filters contrast node does not match"""
    Gamma = 1.0

    # We don't have to run this step, but it ensures the histogram is up to date
    (ActualMosaicMin, ActualMosaicMax) = _GetMinMaxCutoffs(
        calculate_histogram=calculate_histogram,
        min_cutoff=contrast_cutoffs[0],
        max_cutoff=1.0 - contrast_cutoffs[1],
        histogram_cache_path=histogram_cache_path)

    if section_number in contrast_map:
        ActualMosaicMin = ActualMosaicMin if contrast_map[section_number].Min is None else contrast_map[
            section_number].Min
        ActualMosaicMax = ActualMosaicMax if contrast_map[section_number].Max is None else contrast_map[
            section_number].Max
        Gamma = Gamma if contrast_map[section_number].Gamma is None else contrast_map[section_number].Gamma

    return MinMaxGamma(min=ActualMosaicMin,
                       max=ActualMosaicMax,
                       gamma=Gamma)


if __name__ == "__main__":
    pass
