#! /usr/bin/env python
import numpy as np
from pygeotools.lib import iolib,geolib,malib
import os,sys,glob,shutil,psutil
import pandas as pd
import geopandas as gpd
from pyproj import Proj, transform, Transformer
from rpcm import rpc_from_geotiff
from distutils.spawn import find_executable
import subprocess
import ast
from p_tqdm import p_map

n_cpu = psutil.cpu_count(logical=False)
n_cpu_thread = psutil.cpu_count(logical=True)

def run_cmd(bin, args, **kw):
    """
    wrapper around subprocess function to excute bash commands
    Parameters
    ----------
    bin: str
        command to be excuted (e.g., stereo or gdalwarp)
    args: list
        arguments to the command as a list
    Retuns
    ----------
    out: str
        log (stdout) as str if the command executed, error message if the command failed
    """
    #Note, need to add full executable
    #from dshean/vmap.py
    #binpath = os.path.join('/home/sbhushan/src/StereoPipeline/bin',bin)
    binpath = find_executable(bin)
    #if binpath is None:
        #msg = ("Unable to find executable %s\n"
        #"Install ASP and ensure it is in your PATH env variable\n"
       #"https://ti.arc.nasa.gov/tech/asr/intelligent-robotics/ngt/stereo/" % bin)
        #sys.exit(msg)
    #binpath = os.path.join('/opt/StereoPipeline/bin/',bin)
    call = [binpath,]
    #print(call)

    #print(call)
    #print(' '.join(call))
    if args is not None:
        call.extend(args)
    #print(call)
    try:
        out = subprocess.run(call,check=True,capture_output=True,encoding='UTF-8').stdout
    except:
        out = "the command {} failed to run, see corresponding asp log".format(call)
    return out


def read_tsai_dict(tsai):
    """
    read tsai frame model from asp and return a python dictionary containing the parameters
    See ASP's frame camera implementation here: https://stereopipeline.readthedocs.io/en/latest/pinholemodels.html
    Parameters
    ----------
    tsai: str
        path to ASP frame camera model
    Returns
    ----------
    output: dictionary
        dictionary containing camera model parameters
    #TODO: support distortion model
    """
    camera = os.path.basename(tsai)
    with open(tsai,'r') as f:
        content = f.readlines()
    content = [x.strip() for x in content]
    fu = np.float(content[2].split(' = ',4)[1]) # focal length in x
    fv = np.float(content[3].split(' = ',4)[1]) # focal length in y
    cu = np.float(content[4].split(' = ',4)[1]) # optical center in x
    cv = np.float(content[5].split(' = ',4)[1]) # optical center in y
    cam = content[9].split(' = ',10)[1].split(' ')
    cam_cen = [np.float(x) for x in cam] # camera center coordinates in ECEF
    rot = content[10].split(' = ',10)[1].split(' ')
    rot_mat = [np.float(x) for x in rot] # rotation matrix for camera to world coordinates transformation
    pitch = np.float(content[11].split(' = ',10)[1]) # pixel pitch

    ecef_proj = 'EPSG:4978'
    geo_proj = 'EPSG:4326'
    ecef2wgs = Transformer.from_crs(ecef_proj,geo_proj)
    cam_cen_lat_lon = ecef2wgs.transform(cam_cen[0],cam_cen[1],cam_cen[2]) # this returns lat, lon and height
    # cam_cen_lat_lon = geolib.ecef2ll(cam_cen[0],cam_cen[1],cam_cen[2]) # camera center coordinates in geographic coordinates
    tsai_dict = {'camera':camera,'focal_length':(fu,fv),'optical_center':(cu,cv),'cam_cen_ecef':cam_cen,'cam_cen_wgs':cam_cen_lat_lon,'rotation_matrix':rot_mat,'pitch':pitch}
    return tsai_dict

def make_tsai(outfn,cu,cv,fu,fv,rot_mat,C,pitch):
    """
    write out pinhole model with given parameters
    See ASP's frame camera implementation here: https://stereopipeline.readthedocs.io/en/latest/pinholemodels.html
    Parameters
    ----------
    outfn: str
        path where frame camera will be saved
    cu,cv: float/int
        optical center (x,y)
    fu,fv: float/int
        focal length (x,y)
    rot_mat: np.array
         3*3 numpy array of rotation matrix
    C: np.array
         1*3 camera center in ecef coordinates (x,y,z)
    pitch: float/int
        pixel_pitch

    - NOTE:
        # Cameras with ASP's distortion model is currently not implemneted
    """
    out_str = 'VERSION_4\nPINHOLE\nfu = {}\nfv = {}\ncu = {}\ncv = {}\nu_direction = 1 0 0\nv_direction = 0 1 0\nw_direction = 0 0 1\nC = {} {} {}\nR = {} {} {} {} {} {} {} {} {}\npitch = {}\nNULL'.format(fu,fv,cu,cv,C[0],C[1],C[2],rot_mat[0][0],rot_mat[0][1],rot_mat[0][2],rot_mat[1][0],rot_mat[1][1],rot_mat[1][2],rot_mat[2][0],rot_mat[2][1],rot_mat[2][2],pitch)
    with open(outfn,'w') as f:
        f.write(out_str)

def cam_gen(img,fl=553846.153846,cx=1280,cy=540,pitch=1,ht_datum=None,gcp_std=1,out_fn=None,out_gcp=None,datum='WGS84',refdem=None,camera=None,frame_index=None):
        """
        function to initiate frame camera models from input rpc model or frame_index (skysat video)
        Theory: Uses camera resection principle to refine camera extrinsic from given ground control point (for rpc cameras as input, also generates initial camera extrinsic, which is then refined from tandard resection principle)
        See ASP documentation: https://stereopipeline.readthedocs.io/en/latest/tools/cam_gen.html
        Also see simple python implementation: https://github.com/jeffwalton/photogrammetry-resection/blob/master/resection.py.
        Parameters
        ----------
        img: str
            path to image file for which camera is to be generated
        # camera intrinsics:
        fl: float/int
            focal length (default at 553846.153846 px for skysat)
        cx,cy: float/int
            Optical center (default at 1280,540 px for skysat)
        pitch: float/int
            pixel pitch (default at 1, assuming l1a is input, for l1b (surper-resolution), use 0.8)
        # gcp_related_var:
        ht_datum: float/int
            height values to use for getting ground control from corner coordinates, in case missing in DEM
        gcp_std: float/int
            standard deviation to be assigned to gcp (think of it as how accurate you think your gcps are, used as weights by ASP in bundle adjustment, default: 1)
        datum: str
            vertical reference datum (default to WGS84)
        refdem: str
            path to reference DEM to compute the ground control
        camera: str
            path to initial camera (e.g. RPC camera for L1B triplets)
        frame_index: str
            path to frame_index.csv containing attitude ephermis data for L1A skysat videos
        #output filenames:
        out_fn: str
            path to store frame camera model at
        out_gcp: str
            path to store gcp file
        Returns
        ----------
        out: str
            cam_gen log output (STDOUT)
        """
        cam_gen_opt = []
        cam_gen_opt.extend(['--focal-length',str(fl)])
        cam_gen_opt.extend(['--optical-center',str(cx),str(cy)])
        cam_gen_opt.extend(['--pixel-pitch',str(pitch)])
        if ht_datum:
            cam_gen_opt.extend(['--height-above-datum',str(ht_datum)])
        cam_gen_opt.extend(['--gcp-std',str(gcp_std)])
        cam_gen_opt.extend(['-o',out_fn])
        cam_gen_opt.extend(['--gcp-file',out_gcp])
        cam_gen_opt.extend(['--datum',datum])
        cam_gen_opt.extend(['--reference-dem',refdem])
        if camera:
            cam_gen_opt.extend(['--input-camera',camera])
        if frame_index:
            cam_gen_opt.extend(['--frame-index',frame_index])
            cam_gen_opt.extend(['--parse-ecef'])
        cam_gen_opt.extend(['--refine-camera'])
        cam_gen_args = [img]
        #print(cam_gen_opt+cam_gen_args)
        out = run_cmd('cam_gen',cam_gen_args+cam_gen_opt,msg='Running camgen command for image {}'.format(os.path.basename(img)))
        return out

def clean_img_in_gcp(row):
        """
        helper function to return basename of image path
        See clean_gcp function for main implementation
        Parameters
        ----------
        row: dataframe row value to be renamed
        Returns
        ----------
        out: str
            basename of file
        """
        return os.path.basename(row[7])

def clean_gcp(gcp_list,outdir):
    """
    ASP's cam_gen writes full path for images in the GCP files. This does not play well during bundle adjustment.
    The function returns a consolidated gcp file with all images paths only containing basenames so that bundle adjustment can roll along
    See ASP's gcp logic here: https://stereopipeline.readthedocs.io/en/latest/tools/bundle_adjust.html#bagcp
    Parameters
    ----------
    gcp_list: list
        list of gcp paths
    outdir: str
        directory where clean consolidated gcp will be saved as clean_gcp.gcp
    """
    df_list = [pd.read_csv(x,header=None,delimiter=r"\s+") for x in gcp_list]
    gcp_df = pd.concat(df_list, ignore_index=True)
    gcp_df[7] = gcp_df.apply(clean_img_in_gcp,axis=1)
    gcp_df[0] = np.arange(len(gcp_df))
    print(f"Total number of GCPs found {len(gcp_df)}")
    gcp_df.to_csv(os.path.join(outdir,'clean_gcp.gcp'),sep = ' ',index=False,header=False)
    gcp_df.to_csv(os.path.join(outdir,'clean_gcp.csv'),sep = ' ',index=False)

def rpc2map (img,imgx,imgy,imgz=0):
    """
    generate 3D world coordinates from input image pixel coordinates using the RPC model
    See rpcm: https://github.com/cmla/rpcm/blob/master/rpcm/rpc_model.py for implementation
    Parameters
    ----------
    img: str
        path to image file containing RPC in in gdal tags
    imgx,imgy,imgz: int/float
        Image x,y in pixel units, z: height in world coordinates
    Returns
    ----------
    mx,my: np.arrays
        numpy arrays containing longitudes (mx) and latitudes (my) in geographic (EPSG:4326) coordinates
    """
    rpc = rpc_from_geotiff(img)
    mx,my = rpc.localization(imgx,imgy,imgz)
    return mx,my

def get_ba_opts(ba_prefix, camera_weight=0, overlap_list=None, overlap_limit=None, initial_transform=None, input_adjustments=None, flavor='general_ba', session='nadirpinhole', gcp_transform=False,num_iterations=2000,lon_lat_lim=None,elevation_limit=None):
    """
    prepares bundle adjustment cmd for ASP
    most of the parameters are tweaked to handle Planet SkySat data
    See ASP's bundle adjustment documentation: https://stereopipeline.readthedocs.io/en/latest/tools/bundle_adjust.html#
    Parameters
    ----------
    ba_prefix: str
        prefix with which bundle adjustment results will be saved (can be a path, general convention for repo is some path with run prefix, eg., ba_pinhole1/run)
    camera_weight: int/float
        weight to be given to camera extrinsic to allow/prevent their movement during optimazation, default is 0, cameras are allowed to float as much the solver wants to
    overlap_list: str
        path to a text file contianing 2 images per line, which are expected to be overlapping. This limits matching to the pairs in the list only. Very useful for SkySat triplet
    overlap_limit: int
        if images are taken in sequence, the parameter(m) supplied here will only perform matching for an image with its (m) forward neighbours. Very useful for SkySat video
    initial_transform: str
        path to text file from where to apply an initial transform supplied as a 4*4 matrix (such as those output from ASP pc_align)
    input_adjustments: str
        if handling RPC model, this will be path to adjustments from a previous invocation of the program
    flavor: str
        flavors of bundle adjustment to chose from. 'general_ba' will prepare arguments for simple 1 round bundle_adjustment. `2_round_gcp_1` will prepare arguments for fully free camera optimazationwhile `2_round_gcp_2` prepares arguments for only shifting the optimized camera set a hole to the median transform from all gcps. This is genrally a part of 2 step process where `2_round_gcp_2` follows a `2_round_gcp_1` invocation.
    session: str
        bundle adjustment session, default is nadirpinhole (prefered approach for skysat)
    gcp_transform: bool
        tranform using gcp argument, set to true during `2_round_gcp_2`.
    num_iterations: int
        number of solver iterations, default at 2000.
    lon_lat_limit: tuple
        Clip the match point/gcps to lie only within this limit after optimization (min,max) #TODO
    elevation_limit: tuple
        Clip the match point/gcps to lie only within this (min,max) limit after optimization

    Returns
    ----------
    ba_opt: list
        a list of arguments to be run using subprocess command.
    """

    ba_opt = []
    ba_opt.extend(['-o', ba_prefix])
    ba_opt.extend(['--min-matches', '4'])
    ba_opt.extend(['--disable-tri-ip-filter'])
    ba_opt.extend(['--force-reuse-match-files'])
    ba_opt.extend(['--ip-per-tile', '4000'])
    ba_opt.extend(['--ip-inlier-factor', '0.2'])
    ba_opt.extend(['--ip-num-ransac-iterations', '1000'])
    ba_opt.extend(['--skip-rough-homography'])
    ba_opt.extend(['--min-triangulation-angle', '0.0001'])
    ba_opt.extend(['--save-cnet-as-csv'])
    ba_opt.extend(['--individually-normalize'])
    ba_opt.extend(['--camera-weight', str(camera_weight)])
    ba_opt.extend(['-t', session])
    ba_opt.extend(['--remove-outliers-params', '75 3 5 6'])
    # How about adding num random passes here ? Think about it, it might help if we are getting stuck in local minima :)
    if session == 'nadirpinhole':
        ba_opt.extend(['--inline-adjustments'])
    if flavor == '2round_gcp_1':
        ba_opt.extend(['--num-iterations', str(num_iterations)])
        ba_opt.extend(['--num-passes', '3'])
    elif flavor == '2round_gcp_2':
        ba_opt.extend(['--num-iterations', '0'])
        ba_opt.extend(['--num-passes', '1'])
        # gcp_transform=True
        if gcp_transform:
            ba_opt.extend(['--transform-cameras-using-gcp'])
        # maybe add gcp arg here, can be added when function is called as well
    if initial_transform:
        ba_opt.extend(['--initial-transform', initial_transform])
    if input_adjustments:
        ba_opt.extend(['--input-adjustments', input_adjustments])
    if overlap_list:
        ba_opt.extend(['--overlap-list', overlap_list])
    if lon_lat_limit:
        ba_opt.extend(['--lon-lat-limit',str(lon_lat_limit[0]),str(lon_lat_limit[1]),str(lon_lat_limit[2]),str(lon_lat_limit[3])])
    if elevation_limit:
        ba_opt.extend(['--elevation-limit',str(elevation_limit[0]),str(elevation_limit[1])])
    return ba_opt

def mapproject(img,outfn,session='rpc',dem='WGS84',tr=None,t_srs='EPSG:4326',cam=None,ba_prefix=None,extent=None):
    """
    orthorectify input image over a given DEM using ASP's mapproject program.
    See mapproject documentation here: https://stereopipeline.readthedocs.io/en/latest/tools/mapproject.html
    Parameters
    ----------
    img: str
        Path to Raw image to be orthorectified
    outfn: str
        Path to output orthorectified image
    session: str
        type of input camera model (default: rpc)
    dem: str
        path to input DEM over which images will be draped (default: WGS84, orthorectify just over datum)
    tr: str
        target resolution of orthorectified output image (e.g.: '0.9')
    t_srs: str
        target projection of orthorectified output image (default: EPSG:4326)
    cam: str
        if pinhole session, this will be the path to pinhole camera model
    ba_prefix: str
        Bundle adjustment output for RPC camera.
    extent: str
        Projection extent within which to limit mapprojection
    Returns
    ----------
    out: str
        mapproject log
    """
    map_opt = []
    map_opt.extend(['-t',session])
    map_opt.extend(['--t_srs',t_srs])
    if ba_prefix:
        map_opt.extend(['--bundle-adjust-prefix',ba_prefix])
    if extent is not None:
        xmin,ymin,xmax,ymax = extent.split(' ')
        map_opt.extend(['--t_projwin', xmin,ymin,xmax,ymax])
    if tr is not None:
        map_opt.extend(['--tr',tr])

    # for SkySat and Doves, limit to integer values, and 0 as no-data
    map_opt.extend(['--nodata-value',str(0)])
    map_opt.extend(['--ot','UInt16'])

    if cam:
        map_args = [dem,img,cam,outfn]
        if '.xml' in cam:
            print("Input is DG, will use all threads")
            map_opt.extend(['--threads',str(iolib.cpu_count())])
    else:
        map_args = [dem,img,outfn]

    out = run_cmd('mapproject',map_opt+map_args)
    return out

def dem_mosaic(img_list,outfn,tr=None,tsrs=None,stats=None,tile_size=None):
    """
    mosaic  input image list using ASP's dem_mosaic program.
    See dem_mosaic documentation here: https://stereopipeline.readthedocs.io/en/latest/tools/dem_mosaic.html
    Parameters
    ----------
    img_list: list
        List of input images to be mosaiced
    outfn: str
        Path to output mosaiced image
    tr: float/int
        target resolution of orthorectified output image
    t_srs: str
        target projection of orthorectified output image (default: EPSG:4326)
    stats: str
        metric to use for mosaicing
    tile_size: int
        tile size for distributed mosaicing (if less on memory)
    Returns
    ----------
    out: str
        dem_mosaic log
    """

    dem_mosaic_opt = []

    if stats:
        dem_mosaic_opt.extend(['--{}'.format(stats)])
    if (tr is not None) & (ast.literal_eval(tr) is not None):
        dem_mosaic_opt.extend(['--tr', str(tr)])
    if tsrs:
        dem_mosaic_opt.extend(['--t_srs', tsrs])
    dem_mosaic_args = img_list
    if tile_size:
        # will first perform tile-wise vertical mosaicing
        # then blend the result
        dem_mosaic_opt.extend(['--tile-size',str(tile_size)])
        temp_fol = os.path.splitext(outfn)[0]+'_temp'
        dem_mosaic_opt.extend(['-o',os.path.join(temp_fol,'run')])
        out_tile_op = run_cmd('dem_mosaic',dem_mosaic_args+dem_mosaic_opt)
        # query all tiles and then do simple mosaic
        #print(os.path.join(temp_fol,'run-*.tif'))
        mos_tile_list = sorted(glob.glob(os.path.join(temp_fol,'run-*.tif')))
        print(f"Found {len(mos_tile_list)}")
        # now perform simple mosaic
        dem_mos2_opt = []
        dem_mos2_opt.extend(['-o',outfn])
        dem_mos2_args = mos_tile_list
        out_fn_mos = run_cmd('dem_mosaic',dem_mos2_args+dem_mos2_opt)
        out = out_tile_op+out_fn_mos
        print("Deleting tile directory")
        shutil.rmtree(temp_fol)

    else:
        # process all at once, no tiling
        dem_mosaic_opt.extend(['-o',outfn])
        out = run_cmd('dem_mosaic',dem_mosaic_args+dem_mosaic_opt)
    return out

def get_stereo_opts(session='rpc',ep=0,threads=4,ba_prefix=None,align='Affineepipolar',xcorr=2,std_mask=0.5,std_kernel=-1,lv=5,corr_kernel=[21,21],rfne_kernel=[35,35],stereo_mode='asp_bm',spm=1,cost_mode=2,corr_tile_size=1024,mvs=False):
    """
    prepares stereo cmd for ASP
    See ASP's stereo documentation here: https://stereopipeline.readthedocs.io/en/latest/correlation.html
    Parameters
    ----------
    session: str
        camera model with which stereo steps (preprocessing, triangulation will be performed (default: rpc)
    ep: int
        ASP entry point
    threads: int
        number of threads to use for each stereo job (default: 4)
    ba_prefix: str
        if rpc, read adjustment to rpc files from this path
    align: str
        alignment method to be used befor correlation (default: Affineepipolar). Note will only be relevant if non-ortho images are used for correlation
    xcorr: int
        Whether to perform cross-check (forward+backward search during stereo), default is 2, so check for disparity first from left to right and then from right to left
    std_mask: int
        this does not perform what is expected, so omitted now
    std_kernel: int
        omitted for now
    lv: int
        number of pyramidal overview levels for stereo correlation, defualt is 5 levels
    corr_kernel: list
        tempelate window size for stereo correlation (default is [21,21])
    rfne_kernel: list
        tempelate window size for sub-pixel optimization (default is [35,35])
    stereo_mode: str
        asp_bm for block matching, asp_sgm for SGM, asp_mgm for MGM (default is asp_bm)
    spm: int
        subpixel mode, 0 for parabolic localisation, 1 for adaptavie affine and 2 for simple affine (default is 1)
    cost_mode: int
        Cost function to determine match scores, depends on stereo_mode, defualt is 2 (Normalised cross correlation) for block matching
    corr_tile_size: int
        tile sizes for stereo correlation, default is ASP default size of 1024, for SGM/MGM this is changed to 6400 for skysat
    mvs: bool
        if true, prepare arguments for experimental multiview video stereo

    Returns
    ----------
    stereo_opt: list
        a set of stereo arguments as list to be run using subprocess command.
    """
    stereo_opt = []
    # session_args
    stereo_opt.extend(['-t', session])
    stereo_opt.extend(['-e',str(ep)])
    stereo_opt.extend(['--threads-multiprocess', str(threads)])
    stereo_opt.extend(['--threads-singleprocess', str(threads)])
    if ba_prefix:
        stereo_opt.extend(['--bundle-adjust-prefix', ba_prefix])
    # stereo is a python wrapper for 3/4 stages
    # stereo_pprc args : This is for preprocessing (adjusting image dynamic
    # range, alignment using ip matches etc)
    stereo_opt.extend(['--individually-normalize'])
    stereo_opt.extend(['--alignment-method', align])
    stereo_opt.extend(['--ip-per-tile', '8000'])
    stereo_opt.extend(['--ip-num-ransac-iterations','2000'])
    #stereo_opt.extend(['--ip-detect-method', '1'])
    stereo_opt.extend(['--force-reuse-match-files'])
    stereo_opt.extend(['--skip-rough-homography'])
    # mask out completely feature less area using a std filter, to avoid gross MGM errors
    # this is experimental and needs more testing
    stereo_opt.extend(['--stddev-mask-thresh', str(std_mask)])
    stereo_opt.extend(['--stddev-mask-kernel', str(std_kernel)])
    # stereo_corr_args:
    # parallel stereo is generally not required with input SkySat imagery
    # So all the mgm/sgm calls are done without it.
    stereo_opt.extend(['--stereo-algorithm', stereo_mode])
    # the kernel size would depend on the algorithm
    stereo_opt.extend(['--corr-kernel', str(corr_kernel[0]), str(corr_kernel[1])])
    stereo_opt.extend(['--corr-tile-size', str(corr_tile_size)])
    stereo_opt.extend(['--cost-mode', str(cost_mode)])
    stereo_opt.extend(['--corr-max-levels', str(lv)])
    # stereo_rfne_args:
    stereo_opt.extend(['--subpixel-mode', str(spm)])
    stereo_opt.extend(['--subpixel-kernel', str(rfne_kernel[0]), str(rfne_kernel[1])])
    stereo_opt.extend(['--xcorr-threshold', str(xcorr)])
    # stereo_fltr_args:
    """
    Nothing for now,going with default can include somethings like:
    - median-filter-size, --texture-smooth-size (I guess these are set to some defualts for sgm/mgm ?)
    """
    # stereo_tri_args:
    disp_trip = 10000
    if 'map' in session:
        stereo_opt.extend(['--num-matches-from-disparity', str(disp_trip)])
        stereo_opt.extend(['--unalign-disparity'])
    elif not mvs:
        stereo_opt.extend(['--num-matches-from-disp-triplets', str(disp_trip)])
        stereo_opt.extend(['--unalign-disparity'])
    return stereo_opt

def convergence_angle(az1, el1, az2, el2):
    """
    function to calculate convergence angle between two satellites
    # Credits: from David's dgtools
    Parameters
    ----------
    az1,el1: np.array/list/int/float
        azimuth and elevation as arrays/list/single_number (in degrees for satellite 1)
    az2,el2: np.array/list/int/float
        azimuth and elevation as arrays/list/single_number (in degrees for satellite 2)

    Returns
    ----------
    conv_angle: np.array/list/int/float
        convergence angle in degrees
    """
    conv_ang = np.rad2deg(np.arccos(np.sin(np.deg2rad(el1)) * np.sin(np.deg2rad(el2)) + np.cos(np.deg2rad(el1)) * np.cos(np.deg2rad(el2)) * np.cos(np.deg2rad(az1 - az2))))
    return conv_ang

def get_pc_align_opts(outprefix, max_displacement=100, align='point-to-plane', source=True, threads=n_cpu,trans_only=False,initial_align=None):
    """
    prepares ASP pc_align ICP cmd
    See pc_align documentation here: https://stereopipeline.readthedocs.io/en/latest/tools/pc_align.html
    Parameters
    ----------
    outprefix: str
        prefix with which pc_align results will be saved (can be a path, general convention for repo is some path with run prefix, eg., aligned_to/run)
    max_displacement: float/int
        Maximum expected displacement between input DEMs, useful for culling outliers before solving for shifts, default: 100 m
    align: str
        ICP's alignment algorithm to use. default: point-to-plane
    source: bool
        if True, this tells the the algorithm to align the source to reference DEM/PC. If false, this tells the program to align reference to source and save inverse transformation. default: True
    threads: int
        number of threads to use for each stereo job
    trans_only: bool
        if True, this instructs the program to compute translation only when point cloud optimization. Default: False

    Returns
    ----------
    pc_align_opt: list
        list of pc_align parameteres
    """

    pc_align_opts = []
    pc_align_opts.extend(['--alignment-method', align])
    pc_align_opts.extend(['--max-displacement', str(max_displacement)])
    pc_align_opts.extend(['--highest-accuracy'])
    pc_align_opts.extend(['--threads',str(threads)])
    if source:
        pc_align_opts.extend(['--save-transformed-source-points'])
    else:
        pc_align_opts.extend(['--save-inv-transformed-reference-points'])
    if trans_only:
        pc_align_opts.extend(['--compute-translation-only'])
    if initial_align:
        pc_align_opts.extend(['--initial-transform',initial_align])
    pc_align_opts.extend(['-o', outprefix])
    return pc_align_opts

def get_point2dem_opts(tr, tsrs,threads=n_cpu):
    """
    prepares argument for ASP's point cloud gridding algorithm (point2dem) cmd
    Parameters
    ----------
    tr: float/int
        target resolution of output DEM
    tsrs: str
        projection of output DEM
    threads: int
        number of threads to use for each stereo job

    Returns
    ----------
    point2dem_opts: list
        list of point2dem parameteres
    """

    point2dem_opts = []
    point2dem_opts.extend(['--tr', str(tr)])
    point2dem_opts.extend(['--t_srs', tsrs])
    point2dem_opts.extend(['--threads',str(threads)])
    point2dem_opts.extend(['--errorimage'])
    point2dem_opts.extend(['--nodata-value',str(-9999.0)])
    return point2dem_opts

def get_total_shift(pc_align_log):
    """
    returns total shift by pc_align
    Parameters
    ----------
    pc_align_log: str
        path to log file written by ASP pc_align run

    Returns
    ----------
    total_shift: float
        value of applied displacement
    """
    with open(pc_align_log, 'r') as f:
        content = f.readlines()
    substring = 'Maximum displacement of points between the source cloud with any initial transform applied to it and the source cloud after alignment to the reference'
    max_alignment_string = [i for i in content if substring in i]
    total_shift = np.float(max_alignment_string[0].split(':',15)[-1].split('m')[0])
    return total_shift

def dem_align(ref_dem, source_dem, max_displacement, outprefix, align, trans_only=False, threads=n_cpu,initial_align=None):
    """
    This function implements the full DEM alignment workflow using ASP's pc_align and point2dem programs
    See relevent doumentation here:  https://stereopipeline.readthedocs.io/en/latest/tools/pc_align.html
    Parameters
    ----------
    ref_dem: str
        path to reference DEM for alignment
    source_dem: str
        path to source DEM to be aligned
    max_displacement: float
        Maximum expected displacement between input DEMs, useful for culling outliers before solving for shifts, default: 100 m
    outprefix: str
        prefix with which pc_align results will be saved (can be a path, general convention for repo is some path with run prefix, eg., aligned_to/run)
    align: str
        ICP's alignment algorithm to use. default: point-to-plane
    trans_only: bool
        if True, this instructs the program to compute translation only when point cloud optimization. Default: False
    threads: int
        number of threads to use for each stereo job
    """
    # this block checks wheter reference DEM is finer resolution or source DEM
    # if reference DEM is finer resolution, then source is aligned to reference
    # if source DEM is finer, then reference is aligned to source and source is corrected via the inverse transformation matrix of source to reference alignment.
    source_ds = iolib.fn_getds(source_dem)
    ref_ds = iolib.fn_getds(ref_dem)
    source_res = geolib.get_res(source_ds, square=True)[0]
    ref_res = geolib.get_res(ref_ds, square=True)[0]
    tr = source_res
    tsrs = source_ds.GetProjection()
    print(type(tsrs))
    if ref_res <= source_res:
        source = True
        pc_align_args = [ref_dem, source_dem]
        pc_id = 'trans_source.tif'
        pc_align_vec = '-transform.txt'
    else:
        source = False
        pc_align_args = [source_dem, ref_dem]
        pc_id = 'trans_reference.tif'
        pc_align_vec = '-inverse-transform.txt'
    print("Aligning clouds via the {} method".format(align))

    pc_align_opts = get_pc_align_opts(outprefix,max_displacement,align=align,source=source,trans_only=trans_only,initial_align=initial_align,threads=threads)
    pc_align_log = run_cmd('pc_align', pc_align_opts + pc_align_args)
    print(pc_align_log)
    # this try, except block checks for 2 things.
    #- Did the transformed point-cloud got produced ?
    #- was the maximum displacement greater than twice the max_displacement specified by the user ?
      # 2nd condition is implemented for tricky alignement of individual triplet DEMs to reference, as some small DEMs might be awkardly displaced to > 1000 m.
    # if the above conditions are not met, then gridding of the transformed point-cloud into final DEM will not occur.
    try:
        pc = glob.glob(outprefix + '*'+pc_id)[0]
        pc_log = sorted(glob.glob(outprefix+'*'+'log-pc_align*.txt'))[-1] # this will hopefully pull out latest transformation log
    except:
        print("Failed to find aligned point cloud file")
        sys.exit()
    max_disp = get_total_shift(pc_log)
    print("Maximum displacement is {}".format(max_disp))
    if max_disp <= 2*max_displacement:
        grid = True
    else:
        grid = False

    if grid == True:
        point2dem_opts = get_point2dem_opts(tr, tsrs,threads=threads)
        point2dem_args = [pc]
        print("Saving aligned reference DEM at {}-DEM.tif".format(os.path.splitext(pc)[0]))
        p2dem_log = run_cmd('point2dem', point2dem_opts + point2dem_args)
        # create alignment vector with consistent name of alignment vector for camera alignment
        final_align_vector = os.path.join(os.path.dirname(outprefix),'alignment_vector.txt')
        pc_align_vec = glob.glob(os.path.join(outprefix+pc_align_vec))[0]
        print("Creating DEM alignment vector at {final_align_vector}")
        shutil.copy2(pc_align_vec,final_align_vector)
        print(p2dem_log)
    elif grid == False:
        print("aligned cloud not produced or the total shift applied to cloud is greater than 2 times the max_displacement specified, gridding abandoned")

def get_cam2rpc_opts(t='pinhole', dem=None, gsd=None, num_samples=50):
    """
    generates cmd for ASP cam2rpc
    This generates rpc camera models from the optimized frame camera models
    See documentation here: https://stereopipeline.readthedocs.io/en/latest/tools/cam2rpc.html
    Parameters
    ----------
    t: str
        session, or for here, type of input camera, default: pinhole
    dem: str
        path to DEM which will be used for calculating RPC polynomials
    gsd: float
        Expected ground-samplind distance
    num_samples: int
        Sampling for RPC approximation calculation (default=50)
    Returns
    ----------
    cam2rpc_opts: list
        A list of arguments for cam2rpc call.
    """

    cam2rpc_opts = []
    cam2rpc_opts.extend(['--dem-file', dem])
    cam2rpc_opts.extend(['--save-tif-image'])

    # these parameters are not required when providing a DEM
    # the lon-lat range and height-range is not required when sampling points from a DEM
    #dem_ds = iolib.fn_getds(dem)
    #dem_proj = dem_ds.GetProjection()
    #dem = iolib.ds_getma(dem_ds)
    #min_height, max_height = np.percentile(dem.compressed(), (0.01, 0.99))
    #tsrs = epsg2geolib(4326)
    #xmin, ymin, xmax, ymax = geolib.ds_extent(ds, tsrs)
    #cam2rpc_opts.extend(['--height-range', str(min_height), str(max_height)])
    #cam2rpc_opts.extend(['--lon-lat-range', str(xmin),
                        #str(ymin), str(xmax), str(ymax)])
    if gsd:
        cam2rpc_opts.extend(['--gsd', str(gsd)])

    cam2rpc_opts.extend(['--session', t])
    cam2rpc_opts.extend(['--num-samples', str(num_samples)])
    return cam2rpc_opts

def read_pc_align_transform(transformation):
    """
    Read translation and rotation component from pc_aling 4x4 transformation matrix

    Parameters
    ----------
    transformation: str
        path to text file containing pc_align output *transform.txt file
    Returns
    ----------
    pc_align_trans: numpy array
        three element transformation vector (x,y,z)
    pc_align_rot: numpy array
        rotation vector in 3x3 shape
    """
    with open(transformation, 'r') as f:
        content = f.readlines()
    content = [x.strip() for x in content]
    r11, r12, r13, t1 = ' '.join(content[0].split()).split(' ', 15)
    r21, r22, r23, t2 = ' '.join(content[1].split()).split(' ', 15)
    r31, r32, r33, t3 = ' '.join(content[2].split()).split(' ', 15)
    pc_align_rot = np.reshape(np.array(
        [np.float(x) for x in [r11, r12, r13, r21, r22, r23, r31, r32, r33]]), (3, 3))
    pc_align_trans = np.array([np.float(x) for x in [t1, t2, t3]])
    return pc_align_trans, pc_align_rot

def align_cameras(pinhole_tsai, transform, outfolder='None',write=True, rpc=False, dem=None, gsd=None, img=False):
    """
    Align tsai cameras based on pc_align transformation matrix

    Parameters
    ----------
    pinhole_tsai: str
        Path to pinhole tsai camera
    transform: str
        Path to pc_align transformation matrix text file
    outfolder: str
        Path to output folder where aligned cameras will be written
    write: bool
        True, if want to write out aligned cameras, False if not
    rpc: bool
        True if want to compute RPC from the aligned camera models
    dem: str
        Path to DEM to be used is computing RPC camera models
    gsd: float
        Output ground sampling distance to be written to RPC information
    img: str
        Path to image file for which RPC is being written, this is used in computing image dimension while RPC computation

    Returns
    ----------
    out: list
        2 element list containing adjusted camera center and rotation matrix
    """
    tsai_dict = read_tsai_dict(pinhole_tsai)
    cam_cen = np.array(tsai_dict['cam_cen_ecef'])
    cam_rotation = np.reshape(np.array(tsai_dict['rotation_matrix']), (3, 3))
    pc_align_trans, pc_align_rot = read_pc_align_transform(transform)
    cam_cen_adj = np.matmul(pc_align_rot, cam_cen) + pc_align_trans
    cam_rotation_adj = np.matmul(pc_align_rot, cam_rotation)
    outfn = os.path.splitext(tsai_dict['camera'])[0] + '_adj_pc_align.tsai'
    if outfolder:
        outfn = os.path.join(outfolder, outfn)
    if write:
        make_tsai(outfn,tsai_dict['optical_center'][0],tsai_dict['optical_center'][1],tsai_dict['focal_length'][0],tsai_dict['focal_length'][1],cam_rotation_adj,cam_cen_adj,tsai_dict['pitch'])
    if rpc:
        cam2rpc_opts = get_cam2rpc_opts(t='pinhole', dem=dem, gsd=gsd, num_samples=50)
        rpc_xml = os.path.splitext(outfn)[0] + '_rpc_asp.xml'
        cam2rpc_args = [img, outfn, rpc_xml]
        run_cmd('cam2rpc', cam2rpc_opts + cam2rpc_args)
    out = [cam_cen_adj, cam_rotation_adj]
    return out

def read_px_error(content_line,idx):
    """
    Read pixel reprojection error from a text line parsed from ASP bundle_adjust output
    Parameters
    -----------
    content_line: list
        list of str, each string containing 1 line contents of run-final_residuals_no_loss_function_raw_pixels.txt
    idx: np.array
        point indices for which reprojection error needs to be read
    Returns
    -----------
    px,py: np.arrays
        read pixel reprojection error in x and y direction
    """
    pts_array = np.array(content_line)[idx]
    pts = np.char.split(pts_array,', ')
    px = np.array([np.float(x[0]) for x in pts])
    py = np.array([np.float(x[1]) for x in pts])
    return px,py

def compute_cam_px_reproj_err_stats(content_line,idx):
    """
    Compute discriptive pixel reprojection error stats for all points in a given camera and return as dict
    Parameters
    -----------
    content_line: list
        list of str, each string containing 1 line contents of run-final_residuals_no_loss_function_raw_pixels.txt
    idx: np.array
        point indices for which reprojection error needs to be read
    Returns
    -----------
    stats: dictionary
        cumulative descriptive stats for all pixels viewed from a given camera
    """
    px,py = read_px_error(content_line,idx)
    stats = malib.get_stats_dict(np.sqrt(px**2+py**2),full=True)
    return stats

def compute_cam_px_reproj_err_stats_alt(content_fn,idx):
    """
    Compute discriptive pixel reprojection error stats for all points in a given camera and return as dict
    Parameters
    -----------
    content_line: list
        list of str, each string containing 1 line contents of run-final_residuals_no_loss_function_raw_pixels.txt
    idx: np.array
        point indices for which reprojection error needs to be read
    Returns
    -----------
    stats: dictionary
        cumulative descriptive stats for all pixels viewed from a given camera
    """
    with open(content_fn,'r') as f:
        content = f.readlines()
    content = [x.strip() for x in content]
    try:

        px,py = read_px_error(content,idx)
        stats = malib.get_stats_dict(np.sqrt(px**2+py**2),full=True)
    except ValueError:
        stats = {'count': 0,
        'min': 0.0,
        'max': 0.0,
        'ptp': 0.0,
        'mean': 0.0,
        'std': 0.0,
        'nmad': 0.0,
        'med': 0.0,
        'median': 0.0,
        'p16': 0.0,
        'p84': 0.0,
        'spread': 0.0,
        'mode': 0.0}
        pass
    return stats

def camera_reprojection_error_stats_df(pixel_error_fn):
    """
    Return dataframe of descriptive stats for pixel reprojection errors corresponding to each camera
    Parameters
    ------------
    pixel_error_fn: str
        path to run-final_residuals_no_loss_function_raw_pixels.txt or similar, written by ASP bundle_adjust
    Returns
    ------------
    stats_df: Dataframe
        descriptive stats for pixel reprojection errors for each camera
    """
    # read the text file, line by line
    with open(pixel_error_fn,'r') as f:
        content = f.readlines()
    content = [x.strip() for x in content]

    # compute position of camera filename
    camera_indices = []

    for idx,line in enumerate(content):
        # cameras can be of three types, pinhole tsai, rpc embedded in tif or standalone as xml
        if any(substring in line for substring in ['tif','tsai','.xml']):
            camera_indices.append(idx)
    n_cam = len(camera_indices)
    print(f"Total number of cameras are {n_cam}")

    # read indices (line numbers) of pixel points for each camera
    pts_indices = []
    for idx,cam_idx in enumerate(camera_indices):
        if idx != len(camera_indices)-1:
            pts_indices.append(np.arange(cam_idx+1,camera_indices[idx+1]))
        else:
            pts_indices.append(np.arange(cam_idx+1,len(content)))

    # compute statistics for all pixels in 1 camera, in parallel
    stats_list = p_map(compute_cam_px_reproj_err_stats,[content]*n_cam,pts_indices)

    # compose dataframe based on the returned list of dictionaries
    stats_df = pd.DataFrame(stats_list)

    # assign input camera name
    cam_names = np.array(content)[camera_indices]
    temp_cam = np.char.split(np.array(content)[camera_indices],', ')
    stats_df['camera'] = np.array([os.path.basename(x[0]) for x in temp_cam])

    # dataframe is good to go
    return stats_df

def produce_m(lon,lat,m_meridian_offset=0):
    """
    Produce M matrix which facilitates conversion from Lon-lat (NED) to ECEF coordinates
    From https://github.com/visionworkbench/visionworkbench/blob/master/src/vw/Cartography/Datum.cc#L249
    This is known as direction cosie matrix

    Parameters
    ------------
    lon: numeric
        longitude of spacecraft
    lat: numeric
        latitude of spacecraft
    m_meridian_offset: numeric
        set to zero
    Returns
    -----------
    R: np.array
        3 x 3 rotation matrix representing the m-matrix aka direction cosine matrix
    """
    if lat < -90:
        lat = -90
    if lat > 90:
        lat = 90

    rlon = (lon + m_meridian_offset) * (np.pi/180)
    rlat = lat * (np.pi/180)
    slat = np.sin(rlat)
    clat = np.cos(rlat)
    slon = np.sin(rlon)
    clon = np.cos(rlon)

    R = np.ones((3,3),dtype=float)
    R[0,0] = -slat*clon
    R[1,0] = -slat*slon
    R[2,0] = clat
    R[0,1] = -slon
    R[1,1] = clon
    R[2,1] = 0.0
    R[0,2] = -clon*clat
    R[1,2] = -slon*clat
    R[2,2] = -slat
    return R

def convert_ecef2NED(asp_rotation,lon,lat):
    """
    convert rotation matrices from ECEF to North-East-Down convention
    Parameters
    -------------
    asp_rotation: np.array
        3 x 3 rotation matrix from ASP
    lon: numeric
        longitude for computing m matrix
    lat: numeric
        latitude for computing m matrix

    Returns
    --------------
    r_ned: np.array
        3 x 3 NED rotation matrix
    """
    m = produce_m(lon,lat)
    r_ned = np.matmul(np.linalg.inv(m),asp_rotation)
    #r_ned = np.matmul(np.transpose(m),asp_rotation)
    #r_ned = np.matmul(m,asp_rotation)
    return r_ned

def ned_rotation_from_tsai(tsai_fn):
    """
    return yaw pitch and roll angles from a ASP tsai file
    This is experimental and only tested for one SkySat dataset, will remove this message when get consistent results for other datasets

    Parameters
    ------------
    tsai_fn: str
        path to tsai file

    Returns
    ------------
    yaw,pitch,roll: numeric
        yaw pitch and roll angle in degrees (order of rotation assumed: Yaw, Pitch, Roll)
    """
    from scipy.spatial.transform import Rotation as R

    #coordinate conversion step
    from pyproj import Transformer
    ecef_proj = 'EPSG:4978'
    geo_proj = 'EPSG:4326'
    ecef2wgs = Transformer.from_crs(ecef_proj,geo_proj)

    # read tsai files
    asp_dict = asp.read_tsai_dict(tsai_fn)

    # get camera position
    cam_cen = asp_dict['cam_cen_ecef']
    lat,lon,h = ecef2wgs.transform(*cam_cen)
    #print(lat,lon)
    # get camera rotation angle
    rot_mat = np.reshape(asp_dict['rotation_matrix'],(3,3))

    #rotate about z axis by 90 degrees
    #https://math.stackexchange.com/questions/651413/given-the-degrees-to-rotate-around-axis-how-do-you-come-up-with-rotation-matrix
    rot_z = np.zeros((3,3),float)
    angle = np.pi/2
    rot_z[0,0] = np.cos(angle)
    rot_z[0,1] = -1 * np.sin(angle)
    rot_z[1,0] = np.sin(angle)
    rot_z[1,1] = np.cos(angle)
    rot_z[2,2] = 1



    #return np.matmul(rot_z,convert_ecef2NED(rot_mat,lon,lat))
    return R.from_matrix(np.matmul(rot_z,np.linalg.inv(convert_ecef2NED(rot_mat,lon,lat)))).as_euler('ZYX',degrees=True)


def prepare_virtual_gcp(init_reproj_fn,cnet_fn,refdem,out_gcp,dem_crs='EPSG:32644',dh_threshold = 0.75,mask_glac=True):
    """
    *** This is experimental and not tested apart from the Chamoli multi-sensor, multi-orbit dataset***
    Prepare virtual GCP network from initially triangulated pointcloud
    Parameters
    ------------
    init_reproj_fn: str
        path to initial reprojection error file
    cnet_fn: str
        path to initially triangulated control network
    refdem: str
        path to refdem
    out_gcp: str
        Path to output GCP file
    dem_crs: str
        CRS for input DEM
        ## This should not be required, we should get rid of this
    dh_threshold: numeric
        absolute dh threshold between triangulated points and refrence DEM height to select as virtual GCP
    mask_glac: bool
        mask out points within a glacier (**Not implemented rn**)


    """
    print("Initiating logic to compute virtual GCP file")

    print("Step 1: Reading inital reprojection error file......")
    init_gdf = _pointmap2gdf(init_reproj_fn,proj=dem_crs)

    print("Step 2: Reading reference DEM........")
    dem_ds = iolib.fn_getds(refdem)

    print("Step 3: Sampling heights from reference DEM and computing elevation residual")
    map_x = init_gdf.geometry.x.values
    map_y = init_gdf.geometry.y.values
    init_gdf['dem_height'] = _sample_ndimage(iolib.ds_getma(dem_ds),dem_ds.GetGeoTransform(),map_x,map_y)
    init_gdf['dh'] = np.abs(init_gdf['dem_height'] - init_gdf['height_above_datum'])

    print(f"Step 4: Applying absolute input dh threshold of {np.round(dh_threshold,2)} m..............")
    mask = init_gdf['dh'] <= dh_threshold
    fltr_gdf = init_gdf[mask]
    print(f"From total of {len(init_gdf)} points, {len(fltr_gdf)} points fall within dh_threshold ")

    print("Step 5: Preparing GCPs indices")
    filtered_idx = fltr_gdf.index.values
    mask_5_view = fltr_gdf[' num_observations'] >= 5
    five_view_idx = fltr_gdf[mask_5_view].index.values

    mask_4_view = fltr_gdf[' num_observations'] == 4
    four_view_idx = fltr_gdf[mask_4_view].index.values

    mask_3_view = fltr_gdf[' num_observations'] == 3
    three_view_idx = fltr_gdf[mask_3_view].index.values

    mask_2_view = fltr_gdf[' num_observations'] == 2
    two_view_idx = fltr_gdf[mask_2_view].index.values

    print("Step 6: Reading control network")
    with open(cnet_fn,'r') as f:
        content = f.readlines()
    content = [x.strip() for x in content]

    print("Step 7: Writing GCP to disk")
    #outfn = os.path.splitext(cnet_fn)[0]+'_opt3_gcp.gcp'
    counter = 1

    view_count = []
    with open (out_gcp,'w') as f:
        for idx,line in enumerate(tqdm(content)):
            if idx not in filtered_idx:
                continue
            else:

                num_img = line.count('.tif')

                view_count.append(num_img)

                new_str = f"{counter} {line.split(' ',1)[1]}"
                if idx in five_view_idx:
                    #print(new_str)
                    new_str = new_str.split(' 1 1 1 ')[0] + ' 0.5 0.5 0.5 '+new_str.split(' 1 1 1 ')[1]


                elif idx in four_view_idx:
                    new_str = new_str.split(' 1 1 1 ')[0] + ' 1.2 1.2 1.2 '+new_str.split(' 1 1 1 ')[1]
                elif idx in three_view_idx:
                    new_str = new_str.split(' 1 1 1 ')[0] + ' 1.8 1.8 1.8 '+new_str.split(' 1 1 1 ')[1]

                elif idx in two_view_idx:
                    new_str = new_str.split(' 1 1 1 ')[0] + ' 2.2 2.2 2.2 '+new_str.split(' 1 1 1 ')[1]



                #final_gcp_list.append(new_str)
                counter = counter + 1
                f.write(new_str+'\n')
    # save a copy of initial parameters incase needed later
    out_reproj_fn = os.path.splitext(init_reproj_fn)[0]+'_gcp_material.csv'
    out_cnet_fn = os.path.splitext(cnet_fn)[0]+'_gcp_material.csv'
    shutil.copy2(init_reproj_fn,out_reproj_fn)
    shutil.copy2(cnet_fn,out_cnet_fn)

# Helper functions for virtual GCP function

def _df2gdf(df,proj="EPSG:32644",sort_ascending=False):
    #import geopandas as gpd
    df = df.rename(columns={'# lon':'lon',' lat':'lat',' height_above_datum':'height_above_datum',' mean_residual':'mean_residual'})
    gdf = gpd.GeoDataFrame(df,
                           geometry=gpd.points_from_xy(df.lon, df.lat),
                           crs='EPSG:4326')
    gdf = gdf.to_crs(proj)
    if sort_ascending:
        gdf = gdf.sort_values('mean_residual',ascending=True)
    return gdf

def _pointmap2gdf(pointmap,proj='EPSG:32644',sort_ascending=False):
    df = pd.read_csv(pointmap,skiprows=[1])
    return _df2gdf(df,proj,sort_ascending)

def _mapToPixel(mX, mY, geoTransform):
    """Convert map coordinates to pixel coordinates based on geotransform

    Accepts float or NumPy arrays
    GDAL model used here - upper left corner of upper left pixel for mX, mY (and in GeoTransform)
    """
    mX = np.asarray(mX)
    mY = np.asarray(mY)
    if geoTransform[2] + geoTransform[4] == 0:
        pX = ((mX - geoTransform[0]) / geoTransform[1]) - 0.5
        pY = ((mY - geoTransform[3]) / geoTransform[5]) - 0.5
    else:
        pX, pY = applyGeoTransform(mX, mY, invertGeoTransform(geoTransform))
    #return int(pX), int(pY)
    return pX, pY

def _sample_ndimage(dem_ma,dem_gt,map_x,map_y,r='bilinear'):
    """
    sample values from the dem masked array for the points in map_x, map_y coordinates
    dem_ma: Masked numpy array, prefer the dem to be conitnous though
    gt: geotransform of dem/input array
    map_x: x_coordinate array
    map_y: y_coordinate array
    r: resampling algorithm for decimal px location
    out: array containing sampled values at zip(map_y,map_x)
    """
    import scipy.ndimage
    #convert map points to px points using geotransform information
    img_x,img_y = _mapToPixel(map_x,map_y,dem_gt)
    #prepare input for sampling function
    yx = np.array([img_y,img_x])
    # sample the array
    sampled_pts = scipy.ndimage.map_coordinates(dem_ma, yx, order=1,mode='nearest')
    return sampled_pts


def ipfind_ipmatch(img1,img2,subpixel=False,clear_matchfile=True):
    """
    Find match points between two images using SIFT operator in ASP
    Parameters
    -------------
    img1: str
        path to first image
    img2: str
        path to second image
    subpixel: bool
        if True, coordinates with subpixel precision are returned
    clear_matchfile: bool
        if True, will wipe out the ASP produced matchfile from disk
    Returns
    --------------
    match_img1: np.array
        array containing matchpoints coordinates in img1 as (x,y) tuples
    match_img2: np.array
        array containing matchpoints coordinates in img2 as (x,y) tuples
    """
    base1 = os.path.splitext(img1)[0]
    base2 = os.path.splitext(img2)[0]
    #asp.run_cmd('ipfind', ['--normalize','--ip-per-tile','2000',img1])
    #asp.run_cmd('ipfind', ['--normalize','--ip-per-tile','2000',img2])
    asp.run_cmd('ipfind', ['--normalize',img1])
    asp.run_cmd('ipfind', ['--normalize',img2])
    ip1 = base1+'.vwip'
    ip2 = base2+'.vwip'
    asp.run_cmd('ipmatch',[img1,ip1,img2,ip2])
    match_fn = base1+'__'+os.path.basename(base2)+'.match'
    match_img1,match_img2 = read_match_file(match_fn)
    match_img1 = pd.DataFrame(match_img1)
    match_img2 = pd.DataFrame(match_img2)
    os.remove(ip1)
    os.remove(ip2)
    if subpixel:
        match_img1 = np.array(list(zip(match_img1[0].values,match_img1[1].values)))
        match_img2 = np.array(list(zip(match_img2[0].values,match_img2[1].values)))
    else:
        match_img1 = np.array(list(zip(match_img1[2].values,match_img1[3].values)))
        match_img2 = np.array(list(zip(match_img2[2].values,match_img2[3].values)))
    if clear_matchfile:
        os.remove(match_fn)
    return match_img1,match_img2


def read_ip_record(mf):
    """
    Read one IP record from the binary match file.
    #### Reading ip and MP is borrowed from the solution which Amaury Dehecq shared on the ASP mailing list
    #### All credits to Amaury (amaury.dehecq at univ-grenoble-alpes.fr)

    Information comtained are x, y, xi, yi, orientation, scale, interest, polarity, octave, scale_lvl, desc
    (Oleg/Scott to explain?)
    Input: - mf, file handle to the in put binary file (in 'rb' mode)
    Output: - iprec, array containing the IP record
    """
    x, y = np.frombuffer(mf.read(8), dtype=np.float32)
    xi, yi = np.frombuffer(mf.read(8), dtype=np.int32)
    orientation, scale, interest = np.frombuffer(mf.read(12), dtype=np.float32)
    polarity, = np.frombuffer(mf.read(1), dtype=np.int8)  # or np.bool?
    octave, scale_lvl = np.frombuffer(mf.read(8), dtype=np.uint32)
    ndesc, = np.frombuffer(mf.read(8), dtype=np.uint64)
    desc = np.frombuffer(mf.read(int(ndesc * 4)), dtype=np.float32)
    iprec = [x, y, xi, yi, orientation, scale, interest, polarity, octave, scale_lvl, ndesc]
    iprec.extend(desc)
    return iprec

def read_match_file(match_file):
    """
    Read a full binary match file. First two 8-bits contain the number of IPs in each image. Then contains the record for each IP, image1 first, then image2.
    #### Reading ip and MP is borrowed from the solution which Amaury Dehecq shared on the ASP mailing list
    #### All credits to Amaury (amaury.dehecq at univ-grenoble-alpes.fr)

    Input:
    - match_file: str, path to the match file
    Outputs:
    - two arrays, containing the IP records for image1 and image2.
    """

    # Open binary file in read mode
    mf = open(match_file,'rb')

    # Read record length
    size1 = np.frombuffer(mf.read(8), dtype=np.uint64)[0]
    size2 = np.frombuffer(mf.read(8), dtype=np.uint64)[0]

    # Read record for each image
    im1_ip = [read_ip_record(mf) for i in range(size1)]
    im2_ip = [read_ip_record(mf) for i in range(size2)]

    # Close file
    mf.close()

    return im1_ip, im2_ip


def virtual_gcp_ba(img_list,cam_list,overlap_list,session,ba_prefix,
                   refdem,out_gcp,dem_crs='EPSG:32644',dh_threshold = 0.75,mask_glac=True,
                  prepare_matchfiles=False):
    # step 1: prepare matchfiles
    ## Assume for now exists in a directory

    # step 2: run bundle adjust with zero iterations and only 1 pass
    ## this will produce the pointmap file and the cnet.csv file #init_reproj_fn,#cnet_fn
    cnet_ba_opt =  get_ba_opts(
            ba_prefix, session=session,num_iterations=0,num_pass=1,overlap_list=overlap_list,camera_weight=0)
    ba_args = img_list + cam_list
    print("Building control network and pointmap files for virtual GCP creation")
    run_cmd('bundle_adjust', cnet_ba_opt + ba_args)
    try:
        cnet_fn = glob.glob(ba_prefix+'*cnet.csv')[0]
    except:
        print("No control network found, exiting")
        sys.exit()

    try:
        init_reproj_fn = glob.glob(ba_prefix+'*initial*no_loss_*pointmap*.csv')[0]
    except:
        print("No initial error pointmap found,exiting")
        sys.exit()


    # step 3: Run the function from above which will generate the gcp

    prepare_virtual_gcp(init_reproj_fn,cnet_fn,refdem,out_gcp,dem_crs,dh_threshold,mask_glac)

    # Finally run the bundle adjustment with the GCPs
    gcp_bundle_adjust_opt = get_ba_opts(
            ba_prefix, session=session,num_iterations=400,num_pass=1,overlap_list=overlap_list,camera_weight=0)
    print("Running final bundle adjustment using virtual GCPs")
    ba_args = img_list + cam_list + [outgcp]
    run_cmd('bundle_adjust',gcp_bundle_adjust_opt + ba_args)

    print("Tada, you are done !!")
