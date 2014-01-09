nornir-build -volume %1 -pipeline CreateBlobFilter -InputFilter LeveledShadingCorrected -OutputFilter Blob -Levels 1,2,4,8
nornir-build -volume %1 -pipeline AlignSections -AlignDownsample 8 -Filters Blob
nornir-build -volume %1 -pipeline RefineSectionAlignment -Filters LeveledShadingCorrected -InputGroup StosBrute -InputDownsample 8 -OutputGroup Grid -OutputDownsample 8
nornir-build -volume %1 -pipeline RefineSectionAlignment -Filters ShadingCorrected -InputGroup Grid -AlignFilters ShadingCorrected -InputDownsample 8 -OutputGroup Grid -OutputDownsample 2
nornir-build -volume %1 -pipeline ScaleVolumeTransforms -ScaleGroupName Grid -ScaleInputDownsample 4 -ScaleOutputDownsample 1
nornir-build -volume %1 -pipeline SliceToVolume -InputDownsample 1 -InputGroup Grid -OutputGroup SliceToVolume
nornir-build -volume %1 -pipeline VolumeImage -VolumeImageGroupName SliceToVolume -VolumeImageDownsample 1
