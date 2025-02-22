[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.4422248.svg)](https://doi.org/10.5281/zenodo.4422248)

# skysat_stereo
Tools and libraries for processing Planet SkySat imagery, including camera model refinement, stereo reconstruction, and orthomosaic production

## Introduction
Planet operates a constellation of 19 SkySat-C SmallSats, which can acquire very-high-resolution (0.7 m to 0.9 m) triplet stereo and continuous video imagery with short revisit times. This provides an excellent opportunity to derive on-demand, high-resolution Digital Elevation Models (DEMs) for any point on the Earth's surface, with broad applications for Earth science research. However, the quality of these DEMs is currently limited by the geolocation accuracy of the default SkySat camera models, and few existing photogrammetry tools can process the SkySat images.

## Purpose
We developed automated workflows to refine the SkySat camera models and produce accurate DEMs and orthomosaics. For additional details on the workflow and evaluation of output products, please see the corresponding [open-access publication](https://doi.org/10.1016/j.isprsjprs.2020.12.012) in the ISPRS Journal of Photogrammetry and Remote Sensing. This repository contains all tools and libraries as a supplement to the published manuscript. 

This project is under active development and we welcome contributions (information for contributors forthcoming) and preliminary feedback from early visitors (you) :)

## Contents
#### [skysat_stereo](/skysat_stereo) - libraries used throughout the processing workflow
- `asp_utils.py` - library of functions involving components of the NASA Ames Stereo Pipeline
- `skysat.py` - library of functions specific for SkySat processing
- `misc_geospatial.py` - miscelaneous functions for geospatial analysis and image processing

#### [scripts](/scripts/) - command line utilities for the SkySat processing workflow.
1. [`skysat_overlap.py`](/scripts/skysat_overlap.py) - identifies overlapping scenes
2. [`skysat_preprocess.py`](/scripts/skysat_preprocess.py) - prepares subset of video scenes, generates [frame camera models](/docs/sample_camera_models/)
3. [`ba_skysat.py`](/scripts/ba_skysat.py) - bundle adjustment and camera refinement
4. [`skysat_stereo_cli.py`](/scripts/skysat_stereo_cli.py) - stereo reconstruction
5. [`skysat_dem_mos.py`](/scripts/skysat_dem_mos.py) - generates DEM composites with relative accuracy and count metrics
6. [`skysat_pc_cam.py`](/scripts/skysat_pc_cam.py) - point clouds gridding, DEM co-registration, export updated frame and RPC camera models
7. [`skysat_orthorectify.py`](/scripts/skysat_orthorectify.py) - orthorectify individual scenes and produce orthoimage mosaics
8. [`plot_disparity.py`](/scripts/plot_disparity.py) - visualize DEM, disparity map, stereo triangulation intersection error map
9. [`skysat_triplet_pipeline.py`](/scripts/skysat_triplet_pipeline.py) - wrapper script for end to end triplet stereo processing workflow
10. [`skysat_video_pipeline.py`](/scripts/skysat_video_pipeline.py) - wrapper script for end to end video stereo processing workflow
#### [notebooks](/notebooks/) - notebooks used during analysis and figure preparation

## Input products supported
1. Triplet stereo or bi-stereo collections at **L1B level**. These images should be non-georeferenced and should have an accompanying RPC metadata with them. To make sure if you have the rpc information, do a gdalinfo in one of the images, and you should see RPC information printed out. 
2. Video collections at **L1A level**. These images should be non-georeferenced, and should have a frame_index.csv file accompanying them, which contain the ground footprint and satellite attitude/ephemris data.
   
While ordering data from Planet, please make sure the correct data level is specified :)

At this stage, *we do not support the L1A full frame triplet stereo products*, but we have developed an internal workflow logic, and are in the process of writing actual code for it. Thanks for your patience and interest :)

## Sample output products
### SkySat Triplet Stereo
![triplet_product](/docs/img/Figure3.jpg)
Figure 1: Orthoimage mosaic and DEM composite generated from a SkySat triplet stereo collection over Mt. Rainier, WA, USA. These final products were derived from L1B imagery that is &copy; Planet, 2019 (Planet Team, 2017).

![triplet_accuracy](/docs/img/Figure4.jpg)
Figure 2: Relative and absolute accuracy before (using Planet RPCs) and after the `skysat_stereo` correction workflow.

### SkySat Video
![video_samples](/docs/img/Figure5.jpg)
Figure 3: Sample products from SkySat video collection over Mt. St. Helen's crater (after `skysat_stereo` correction workflow). These final products were derived from L1A imagery that is &copy; Planet, 2019 (Planet Team, 2017).

## Dependencies
- See [environment.yml file](/environment.yml) for complete list of Python packages with pinned version numbers.
- [NASA Ames Stereo Pipeline v 3.0.1 alpha (April 22 2022)](https://stereopipeline.readthedocs.io/en/latest/)

## Installation
Please see the [install instructions](/docs/install_instructions.md).

Notes:
* These tools were developed and tested on a dedicated [Broadwell node](https://www.nas.nasa.gov/hecc/resources/pleiades.html) on the NASA Pleiades supercomputer, running SUSE Linux Enterprise Server. 
* Many tools use parallel threads and/or processes, and the hardcoded number of threads and processes were tuned based on the available resources (28 CPUs, 128 GB RAM).  Some utilities should autoscale based on available resources, but others may require modifications for optimization on other systems.
* The code should work for \*nix platforms. We have not tested on Windows. 

## License
This project is licensed under the terms of the [MIT License](./LICENSE).

## Citation
If you use this code and/or derived products in a scientific publication, please cite:
* Bhushan, S., Shean, D., Alexandrov, O., & Henderson, S. (2021). Automated digital elevation model (DEM) generation from very-high-resolution Planet SkySat triplet stereo and video imagery. *ISPRS Journal of Photogrammetry and Remote Sensing*, 173, 151–165. https://doi.org/10.1016/j.isprsjprs.2020.12.012.
* Shashank Bhushan, David Shean, Oleg Alexandrov, & Scott Henderson. (2021, January 7). uw-cryo/skysat_stereo: Zenodo doi revision updates (Version 0.2). Zenodo. http://doi.org/10.5281/zenodo.4422248

## Funding and Acknowledgments
* This research was supported by the NASA Terrestrial Hydrology Program (THP) and the NASA Cryosphere Program. Shashank Bhushan was supported by a NASA FINESST award (80NSSC19K1338) and the NASA HiMAT project (NNX16AQ88G). David Shean, Oleg Alexandrov and Scott Henderson were supported by NASA THP award 80NSSC18K1405. SkySat tasking, data access, and supplemental support was provided under the [NASA Commercial Smallsat Data Acquisition Program 2018 Pilot Study](https://sit.earthdata.nasa.gov/about/small-satellite-commercial-data-buy-program)
* We acknowledge Compton J. Tucker and others at NASA Goddard Space Flight Center and NASA Headquarters for coordinating the Commercial Satellite Data Access Program Pilot and assisting with prelimnary SkySat tasking campaigns. Paris Good at Planet provided invaluable assistance with data acquisition and facilitated discussions with Planet engineering teams. Thanks are also due to Ross Losher, Antonio Martos, Kelsey Jordahl and others at Planet for initial guidance on SkySat-C sensor specifications and camera models. Resources supporting this work were provided by the NASA High-End Computing (HEC) Program through the NASA Advanced Supercomputing (NAS) Division at Ames Research Center. Friedrich Knuth and Michelle Hu provided feedback on initial manuscript outline, code development and documentation. We also acknowledge input from the larger ASP community during photogrammetry discussions.

## References
* Beyer, Ross A., Oleg Alexandrov, and Scott McMichael. 2018. The Ames Stereo Pipeline: NASA's open source software for deriving and processing terrain data, *Earth and Space Science*, 5. https://doi.org/10.1029/2018EA000409.
* Shean, D. E., O. Alexandrov, Z. Moratto, B. E. Smith, I. R. Joughin, C. C. Porter, Morin, P. J. 2016. An automated, open-source pipeline for mass production of digital elevation models (DEMs) from very high-resolution commercial stereo satellite imagery. *ISPRS Journal of Photogrammetry and Remote Sensing*, 116. https://doi.org/10.1016/j.isprsjprs.2016.03.012.
* Ross Beyer, Oleg Alexandrov, ScottMcMichael, Michael Broxton, Mike Lundy, Kyle Husmann, … jlaura. (2020, July 28). NeoGeographyToolkit/StereoPipeline 2.7.0 (Version 2.7.0). Zenodo. http://doi.org/10.5281/zenodo.3963341
* Planet application program interface:  In space for life on earth. San Francisco, CA. https://api.planet.com.
