#!/usr/bin/env python3

# CDDL HEADER START
#
# The contents of this file are subject to the terms of the
# Common Development and Distribution License (the "License").
# You may not use this file except in compliance with the License.
#
# See LICENSE.txt included in this distribution for the specific
# language governing permissions and limitations under the License.
#
# When distributing Covered Code, include this CDDL HEADER in each
# file and include the License file at LICENSE.txt.
# If applicable, add the following below this CDDL HEADER, with the
# fields enclosed by brackets "[]" replaced with your own identifying
# information: Portions Copyright [yyyy] [name of copyright owner]
#
# CDDL HEADER END

#
# Copyright (c) 2018, Oracle and/or its affiliates. All rights reserved.
#

"""
    This script is wrapper of commands to add/remove project or refresh
    configuration using read-only configuration.
"""


import os
from os import path
import sys
import argparse
import filelock
from filelock import Timeout
from command import Command
import logging
import tempfile
import shutil
from utils import get_command
from opengrok import get_configuration, set_configuration, add_project, \
    delete_project, get_config_value


MAJOR_VERSION = sys.version_info[0]
if (MAJOR_VERSION < 3):
    print("Need Python 3, you are running {}".format(MAJOR_VERSION))
    sys.exit(1)

__version__ = "0.2"


def exec_command(doit, logger, cmd, msg):
    """
    Execute given command and return its output.
    Exit the program on failure.
    """
    cmd = Command(cmd, logger=logger)
    if not doit:
        logger.info(cmd)
        return
    cmd.execute()
    if cmd.getstate() is not Command.FINISHED or cmd.getretcode() != 0:
        logger.error(msg)
        logger.error(cmd.getoutput())
        sys.exit(1)

    return cmd.getoutput()


def get_config_file(basedir):
    """
    Return configuration file in basedir
    """

    return path.join(basedir, "etc", "configuration.xml")


def install_config(doit, src, dst):
    """
    Copy the data of src to dst. Exit on failure.
    """
    if not doit:
        logger.debug("Not copying {} to {}".format(src, dst))
        return

    #
    # Copy the file so that close() triggered unlink()
    # does not fail.
    #
    logger.debug("Copying {} to {}".format(src, dst))
    try:
        shutil.copyfile(src, dst)
    except PermissionError:
        logger.error('Failed to copy {} to {} (permissions)'.
                     format(src, dst))
        sys.exit(1)
    except OSError:
        logger.error('Failed to copy {} to {} (I/O)'.
                     format(src, dst))
        sys.exit(1)


def config_refresh(doit, logger, basedir, uri, configmerge, roconfig):
    """
    Refresh current configuration file with configuration retrieved
    from webapp. If roconfig is not None, the current config is merged with
    readonly configuration first.

    The merge of the current config from the webapp with the read-only config
    is done as a workaround for https://github.com/oracle/opengrok/issues/2002
    """

    main_config = get_config_file(basedir)
    if not path.isfile(main_config):
        logger.error("file {} does not exist".format(main_config))
        sys.exit(1)

    if doit:
        current_config = get_configuration(logger, uri)
        if not current_config:
            sys.exit(1)
    else:
        current_config = None

    with tempfile.NamedTemporaryFile() as fcur:
        logger.debug("Temporary file for current config: {}".format(fcur.name))
        if doit:
            fcur.write(bytearray(''.join(current_config), "UTF-8"))

        if not roconfig:
            logger.info('Refreshing configuration')
            install_config(doit, fcur.name, main_config)
        else:
            logger.info('Refreshing configuration '
                        '(merging with read-only config)')
            merged_config = exec_command(doit, logger,
                                         [configmerge, roconfig, fcur.name],
                                         "cannot merge configuration")
            with tempfile.NamedTemporaryFile() as fmerged:
                logger.debug("Temporary file for merged config: {}".
                             format(fmerged.name))
                if doit:
                    fmerged.write(bytearray(''.join(merged_config), "UTF-8"))
                    install_config(doit, fmerged.name, main_config)


def project_add(doit, logger, project, uri):
    """
    Adds a project to configuration. Works in multiple steps:

      1. add the project to configuration
      2. refresh on disk configuration
    """

    logger.info("Adding project {}".format(project))

    if doit:
        add_project(logger, project, uri)


def project_delete(doit, logger, project, uri):
    """
    Delete the project for configuration and all its data.
    Works in multiple steps:

      1. delete the project from configuration and its indexed data
      2. refresh on disk configuration
      3. delete the source code for the project
    """

    # Be extra careful as we will be recursively removing directory structure.
    if not project or len(project) == 0:
        raise Exception("invalid call to project_delete(): missing project")

    logger.info("Deleting project {} and its index data".format(project))

    if doit:
        delete_project(logger, project, uri)

    src_root = get_config_value(logger, 'sourceRoot', uri)
    if not src_root:
        raise Exception("Could not get source root")

    src_root = src_root[0].rstrip()
    logger.debug("Source root = {}".format(src_root))
    if not src_root or len(src_root) == 0:
        raise Exception("source root empty")
    sourcedir = path.join(src_root, project)
    logger.debug("Removing directory tree {}".format(sourcedir))
    if doit:
        logger.info("Removing source code under {}".format(sourcedir))
        shutil.rmtree(sourcedir)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='grok configuration '
                                     'management.',
                                     formatter_class=argparse.
                                     ArgumentDefaultsHelpFormatter)
    parser.add_argument('-D', '--debug', action='store_true',
                        help='Enable debug prints')
    parser.add_argument('-b', '--base', default="/var/opengrok",
                        help='OpenGrok instance base directory')
    parser.add_argument('-R', '--roconfig',
                        help='OpenGrok read-only configuration file')
    parser.add_argument('-U', '--uri', default='http://localhost:8080/source',
                        help='uri of the webapp with context path')
    parser.add_argument('-c', '--configmerge',
                        help='path to the ConfigMerge binary')
    parser.add_argument('-u', '--upload', action='store_true',
                        help='Upload configuration at the end')
    parser.add_argument('-n', '--noop', action='store_false', default=True,
                        help='Do not run any commands or modify any config'
                        ', just report. Usually implies the --debug option.')

    group = parser.add_mutually_exclusive_group()
    group.add_argument('-a', '--add', metavar='project', nargs='+',
                       help='Add project (assumes its source is available '
                       'under source root')
    group.add_argument('-d', '--delete', metavar='project', nargs='+',
                       help='Delete project and its data and source code')
    group.add_argument('-r', '--refresh', action='store_true',
                       help='Refresh configuration. If read-only '
                       'configuration is supplied, it is merged with current '
                       'configuration.')

    args = parser.parse_args()

    #
    # Setup logger as a first thing after parsing arguments so that it can be
    # used through the rest of the program.
    #
    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(format="%(message)s", level=logging.INFO)

    logger = logging.getLogger(os.path.basename(sys.argv[0]))

    # Set the base directory
    if args.base:
        if path.isdir(args.base):
            logger.debug("Using {} as instance base".
                         format(args.base))
        else:
            logger.error("Not a directory: {}\n"
                         "Set the base directory with the --base option."
                         .format(args.base))
            sys.exit(1)

    # read-only configuration file.
    if args.roconfig:
        if path.isfile(args.roconfig):
            logger.debug("Using {} as read-only config".format(args.roconfig))
        else:
            logger.error("File {} does not exist".format(args.roconfig))
            sys.exit(1)

    uri = args.uri
    if not uri:
        logger.error("uri of the webapp not specified")
        sys.exit(1)

    configmerge_file = get_command(logger, args.configmerge, "ConfigMerge")
    if not configmerge_file:
        logger.error("Use the --configmerge option to specify the path to"
                     "the ConfigMerge script")
        sys.exit(1)

    lock = filelock.FileLock(os.path.join(tempfile.gettempdir(),
                             os.path.basename(sys.argv[0]) + ".lock"))
    try:
        with lock.acquire(timeout=0):
            if args.add:
                for proj in args.add:
                    project_add(doit=args.noop, logger=logger,
                                project=proj,
                                uri=uri)

                config_refresh(doit=args.noop, logger=logger,
                               basedir=args.base,
                               uri=uri,
                               configmerge=configmerge_file,
                               roconfig=args.roconfig)
            elif args.delete:
                for proj in args.delete:
                    project_delete(doit=args.noop, logger=logger,
                                   project=proj,
                                   uri=uri)

                config_refresh(doit=args.noop, logger=logger,
                               basedir=args.base,
                               uri=uri,
                               configmerge=configmerge_file,
                               roconfig=args.roconfig)
            elif args.refresh:
                config_refresh(doit=args.noop, logger=logger,
                               basedir=args.base,
                               uri=uri,
                               configmerge=configmerge_file,
                               roconfig=args.roconfig)
            else:
                parser.print_help()
                sys.exit(1)

            if args.upload:
                main_config = get_config_file(basedir=args.base)
                if path.isfile(main_config):
                    if args.noop:
                        set_configuration(logger, main_config, uri)

                else:
                    logger.error("file {} does not exist".format(main_config))
                    sys.exit(1)
    except Timeout:
        logger.warning("Already running, exiting.")
        sys.exit(1)
