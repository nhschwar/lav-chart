TODOs

Ideas for future

Make the pdf scan to md python script sthat got me to the .md script - I had a thorw away version that wasn't included in this repo.

Better way of stripping scripts? Maybe a prompt for hunks for feedback of what to keep like with git add -p

Cast list + Script actors parsing to scene_data.json?

* Maybe we make an assumption if all scenes are headers, and all character names are in bold (or formatted with a : ) I should be able to turn that into a json input in combination with the cast list

* Basically I want to get from scanned pdf or digital version of a script + cast list to lav chart and all outputs

Chain together all existing scripts to be called with a bash script, with input folder, and output folder. Scripts that need outputs from earlier python scripts will draw from output folder.

Embed versioning data in output files - if I have to change one of my scenes to a short scene, or put someone on stage when there isn't a line for them and they don't have enough time for a change, or other manual change to generation, I want to be able to see v4 or something in the metadata or subtitles