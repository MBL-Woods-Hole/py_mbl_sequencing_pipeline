#!/usr/bin/env python

##!/usr/local/www/vamps/software/python/bin/python

##!/usr/bin/env python
# -*- coding: utf-8 -*-

# Copyright (C) 2011, Marine Biological Laboratory
#
# This program is free software; you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version.
#
# Please read the COPYING file.
#

import os
from stat import * # ST_SIZE etc
import sys
import shutil
import types
from time import sleep
from pipeline.utils import *
from pipeline.sample import Sample
from pipeline.runconfig import RunConfig
from pipeline.run import Run
from pipeline.chimera import Chimera
from pipeline.gast import Gast
from pipeline.vamps import Vamps
from pipeline.pipelinelogging import logger
from pipeline.trim_run import TrimRun
import logging
import json    
#sys.path.append("/bioware/pythonmodules/fastalib")
import pipeline.fastalib as u
from pipeline.fasta_mbl_pipeline import MBLPipelineFastaUtils
from pipeline.db_upload import MyConnection, dbUpload 

TRIM_STEP = "trim"
CHIMERA_STEP = "chimera"
GAST_STEP = "gast"
VAMPSUPLOAD = "vampsupload"
ENV454UPLOAD = "env454upload"

existing_steps = [TRIM_STEP, CHIMERA_STEP, GAST_STEP, ENV454UPLOAD, VAMPSUPLOAD]

# the main loop for performing each of the user's supplied steps
def process(run, steps):
    # create output directory:
    requested_steps = steps.split(",")            
    
    if not os.path.exists(run.output_dir):
        logger.debug("Creating output directory: "+run.output_dir)
        os.makedirs(run.output_dir)      
                    
    # loop through official list...this way we execute the
    # users requested steps in the correct order                
    for step in requested_steps:
        if step not in existing_steps:
            print "Invalid processing step: " + step
            sys.exit()
        else:
            # call the method in here
            step_method = globals()[step]
            step_method(run)


# perform trim step
# TrimRun.trimrun() does all the work of looping over each input file and sequence in each file
# all the stats are kept in the trimrun object
#
# when complete...write out the datafiles for the most part on a lane/runkey basis
#
def trim(run):
    # (re) create the trim status file
    run.trim_status_file_h = open(run.trim_status_file_name, "w")
    
    # do the trim work
    mytrim = TrimRun(run) 
    
    # pass True to write out the straight fasta file of all trimmed non-deleted seqs
    # Remember: this is before chimera checking
    trim_codes = mytrim.trimrun(True)
    trim_results_dict = {}
    if trim_codes[0] == 'SUCCESS':
        # setup to write the status
        new_lane_keys = trim_codes[2]
        trim_results_dict['status'] = "success"
        trim_results_dict['new_lane_keys'] = new_lane_keys
        logger.debug("Trimming finished successfully")
        # write the data files
        mytrim.write_data_files(new_lane_keys)
        run.trim_status_file_h.write(json.dumps(trim_results_dict))
        run.trim_status_file_h.close()
    else:
        logger.debug("Trimming finished ERROR")
        trim_results_dict['status'] = "error"
        trim_results_dict['code1'] = trim_codes[1]
        trim_results_dict['code2'] = trim_codes[2]
        run.trim_status_file_h.write(json.dumps(trim_results_dict))
        run.trim_status_file_h.close()
        sys.exit()

# chimera assumes that a trim has been run and that there are files
# sitting around that describe the results of each lane:runkey sequences
# it also expectes there to be a trim_status.txt file around
# which should have a json format with status and the run keys listed        
def chimera(run):
    chimera_cluster_ids = [] 
    logger.debug("Starting Chimera Checker")
    # lets read the trim status file out here and keep those details out of the Chimera code
    new_lane_keys = convert_unicode_dictionary_to_str(json.loads(open(run.trim_status_file_name,"r").read()))["new_lane_keys"]
    mychimera = Chimera(run)
    c_den    = mychimera.chimera_denovo(new_lane_keys)
    if c_den[0] == 'SUCCESS':
        chimera_cluster_ids += c_den[2]
        chimera_code='PASS'
    elif c_den[0] == 'NOREGION':
        chimera_code='NOREGION'
    elif c_den[0] == 'FAIL':
        chimera_code = 'FAIL'
    else:
        chimera_code='FAIL'
    
    c_ref    = mychimera.chimera_reference(new_lane_keys)
    
    if c_ref[0] == 'SUCCESS':
        chimera_cluster_ids += c_ref[2]
        chimera_code='PASS'
    elif c_ref[0] == 'NOREGION':
        chimera_code = 'NOREGION'
    elif c_ref[0] == 'FAIL':
        chimera_code='FAIL'
    else:
        chimera_code='FAIL'
    
    #print chimera_cluster_ids
    run.chimera_status_file_h = open(run.chimera_status_file_name,"w")
    if chimera_code == 'PASS':  
        
        chimera_cluster_code = wait_for_cluster_to_finish(chimera_cluster_ids) 
        if chimera_cluster_code[0] == 'SUCCESS':
            logger.info("Chimera checking finished successfully")
            run.chimera_status_file_h.write("CHIMERA SUCCESS\n")
            
            
        else:
            logger.info("Chimera checking Failed")
            run.chimera_status_file_h.write("CHIMERA ERROR: "+str(chimera_cluster_code[1])+" "+str(chimera_cluster_code[2])+"\n")
            sys.exit()
            
    elif chimera_code == 'NOREGION':
        logger.info("No regions found that need chimera checking")
        run.chimera_status_file_h.write("CHIMERA CHECK NOT NEEDED\n")
        
    elif chimera_code == 'FAIL':
        logger.info("Chimera checking Failed")
        run.chimera_status_file_h.write("CHIMERA ERROR: \n")
        sys.exit()
    else:
        logger.info("Chimera checking Failed")
        run.chimera_status_file_h.write("CHIMERA ERROR: \n")
        sys.exit()
    sleep(2)   
    if  chimera_code == 'PASS' and  chimera_cluster_code[0] == 'SUCCESS':
        mychimera.write_chimeras_to_deleted_file(new_lane_keys)
        # should also recreate fasta
        # then read chimera files and place (or replace) any chimeric read_id
        # into the deleted file.
        
        mymblutils = MBLPipelineFastaUtils(new_lane_keys, mychimera.outdir)
        
        # write new cleaned files that remove chimera if apropriate
        # these are in fasta_mbl_pipeline.py
        # the cleaned file are renamed to the original name:
        # lane_key.unique.fa
        # lane_key.trimmed.fa
        # lane_key.names        -- 
        # lane_key.abund.fa     -- this file is for the uclust chimera script
        # lane_key.deleted.txt  -- no change in this file
        # THE ORDER IS IMPORTANT HERE:
        mymblutils.write_clean_fasta_file()
        mymblutils.write_clean_names_file()
        mymblutils.write_clean_uniques_file()
        mymblutils.write_clean_abundance_file()
        # write keys file for each lane_key - same fields as db table? for easy writing
        # write primers file for each lane_key
 
        
        # Write new clean files to the database
        # rawseq table not used
        # trimseq
        # runkeys
        # primers
        # run primers
        mymblutils.write_clean_files_to_database()

def env454upload(run):  
    
    my_env454upload = dbUpload(run)
    filenames = my_env454upload.get_fasta_file_names(my_env454upload.fasta_dir)
#    print "filenames = %s" % filenames

    for filename in filenames:
        try:
            fasta_file_path = my_env454upload.fasta_dir + filename
            fasta           = u.SequenceSource(fasta_file_path) 
            filename_base   = filename.split("-")[0]
            run_info_ill_id = my_env454upload.get_run_info_ill_id(filename_base)
            gast_dict       = my_env454upload.get_gasta_result(filename)

            while fasta.next():
#                print "fasta.seq = %s" % fasta.seq
                my_env454upload.insert_seq(fasta.seq)
                my_env454upload.insert_pdr_info(fasta, run_info_ill_id)

                (taxonomy, distance, rank, refssu_count, vote, minrank, taxa_counts, max_pcts, na_pcts, refhvr_ids) = gast_dict[fasta.id]
                my_env454upload.insert_taxonomy(taxonomy)


        except Exception, e:          # catch all deriving from Exception (instance e)
            print "Exception: ", e.__str__()      # address the instance, print e.__str__()
#            raise                       # re-throw caught exception   
        except:                       # catch everything
            print "Unexpected:"         # handle unexpected exceptions
            print sys.exc_info()[0]     # info about curr exception (type,value,traceback)
            raise                       # re-throw caught exception   

    
    # for vamps 'new_lane_keys' will be prefix 
    # of the uniques and names file
    # that was just created in vamps_gast.py
#    if(run.vamps_user_upload):
#        lane_keys = [run.user+run.runcode]        
#    else:
#        lane_keys = convert_unicode_dictionary_to_str(json.loads(open(run.trim_status_file_name,"r").read()))["new_lane_keys"]
    
#    print "PPP anchors = %s, base_output_dir = %s, base_python_dir = %s, chimera_status_file_h = %s, chimera_status_file_name = %s,\n\
#     force_runkey = %s, gast_input_source = %s, initializeFromDictionary = %s, input_dir = %s, input_file_info = %s, maximumLength = %s,\n\
#      minAvgQual = %s, minimumLength = %s, output_dir = %s, platform = %s, primer_suites = %s, require_distal = %s, run_date = %s, \n\
#      run_key_lane_dict = %s, run_keys = %s, samples = %s, sff_files = %s, trim_status_file_h = %s, trim_status_file_name = %s, vamps_user_upload = %s\n" % (run.anchors, run.base_output_dir, run.base_python_dir, run.chimera_status_file_h, run.chimera_status_file_name, run.force_runkey, run.gast_input_source, run.initializeFromDictionary, run.input_dir, run.input_file_info, run.maximumLength, run.minAvgQual, run.minimumLength, run.output_dir, run.platform, run.primer_suites, run.require_distal, run.run_date, run.run_key_lane_dict, run.run_keys, run.samples, run.sff_files, run.trim_status_file_h, run.trim_status_file_name, run.vamps_user_upload)
#   dir(run) = ['__doc__', '__init__', '__module__', 'anchors', 'base_output_dir', 'base_python_dir', 'chimera_status_file_h', 
#'chimera_status_file_name', 'force_runkey', 'gast_input_source', 'initializeFromDictionary', 'input_dir', 'input_file_info', 'maximumLength', 
#'minAvgQual', 'minimumLength', 'output_dir', 'platform', 'primer_suites', 'require_distal', 'run_date', 'run_key_lane_dict', 'run_keys', 'samples', 
#'sff_files', 'trim_status_file_h', 'trim_status_file_name', 'vamps_user_upload']

#    logger.debug("PPP run.rundate = ")
#    logger.debug(run.rundate)
#    my_env454upload.select_run(lane_keys)


def gast(run):  
    
    mygast = Gast(run)
    
    # for vamps 'new_lane_keys' will be prefix 
    # of the uniques and names file
    # that was just created in vamps_gast.py
    if(run.vamps_user_upload):
        lane_keys = [run.user+run.runcode]        
    else:
        lane_keys = convert_unicode_dictionary_to_str(json.loads(open(run.trim_status_file_name,"r").read()))["new_lane_keys"]
        
    mygast.clustergast(lane_keys)
    sleep(5)
    mygast.gast_cleanup(lane_keys)
    sleep(5)
    mygast.gast2tax(lane_keys)

    
def vampsupload(run):
    
    myvamps = Vamps(run)
    
    if(run.vamps_user_upload):
        lane_keys = [run.user+run.runcode]        
    else:
        lane_keys = convert_unicode_dictionary_to_str(json.loads(open(run.trim_status_file_name,"r").read()))["new_lane_keys"]
        
    myvamps.taxonomy(lane_keys)
    #myvamps.sequences(lane_keys)        
    #myvamps.exports(lane_keys)
    #myvamps.projects(lane_keys)
    #myvamps.info(lane_keys)
        
        
