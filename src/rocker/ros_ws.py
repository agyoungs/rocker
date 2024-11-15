"""Extension for handling ROS workspaces in Rocker containers."""

import copy
import os
import tempfile
import xml.etree.ElementTree as ET
import pkgutil

import em
from vcstools import VcsClient

from rocker.core import get_user_name
from rocker.extensions import RockerExtension
from rocker.volume_extension import Volume


class RosWs(RockerExtension):
    """
    Extension for handling ROS workspaces in Rocker containers.

    This extension enables mounting and building ROS workspaces inside Docker
    containers, with support for dependency management and workspace
    configuration.
    """

    name = "ros_ws"

    @classmethod
    def get_name(cls):
        """Return the extension's name."""
        return cls.name

    def __init__(self):
        """Initialize the extension."""
        self._env_subs = None
        self.name = RosWs.get_name()
   
    @staticmethod
    def is_workspace_volume(workspace):
        """Check if the workspace is a valid volume."""
        if os.path.isdir(os.path.expanduser(workspace)):
            return True
        else:
            return False

    def get_docker_args(self, cliargs):
        """Get Docker arguments for mounting the workspace volume."""
        workspace = cliargs[self.name]
        if RosWs.is_workspace_volume(workspace):
            args = Volume.get_volume_args(
                [[os.path.expanduser(workspace) + ":" +
                  os.path.join(RosWs.get_home_dir(cliargs),
                               self.name, 'src')]])
            return ' '.join(args)
        else:
            return ''

    def precondition_environment(self, cliargs):
        """Prepare the environment before running the container."""
        pass

    def validate_environment(self, cliargs):
        """Validate the environment before running the container."""
        pass

    def get_preamble(self, cliargs):
        """Return the preamble for the Dockerfile."""
        return ""

    def get_files(self, cliargs):
        """Get the files to be included in the Docker build context."""
        def get_files_from_path(path,
                                only_ros_pacakges=False,
                                is_ros_package=False):
            if os.path.isdir(path):
                # ignoring the .git directory allows the docker build context
                # to cache the build context if the directories weren't modified
                if (not os.path.basename(path) == ".git"):
                    if not is_ros_package:
                        is_ros_package = os.path.exists(os.path.join(path, 'package.xml'))
                    for basename in os.listdir(path):
                        yield from get_files_from_path(os.path.join(path, basename), only_ros_pacakges=only_ros_pacakges, is_ros_package=copy.copy(is_ros_package))
            else:
                if not only_ros_pacakges:
                    yield path
                if only_ros_pacakges and is_ros_package:
                    yield path
        
        def generate_ws_files(dir, only_ros_pacakges=False):
            ws_files = {}
            for filepath in get_files_from_path(os.path.expanduser(dir), only_ros_pacakges=only_ros_pacakges):
                if os.path.islink(filepath):
                    # todo handle symlinks
                    print(f"Warning: Could not copy symlink {filepath} -> {os.readlink(filepath)}")
                    continue
                try:
                    with open(filepath, "r") as f:
                        ws_files[filepath.replace(os.path.expanduser(dir), "ros_ws_src" + os.path.sep)] = f.read()
                except UnicodeDecodeError:
                    # read the file as binary instead
                    with open(filepath, "rb") as f:
                        ws_files[filepath.replace(os.path.expanduser(dir), "ros_ws_src" + os.path.sep)] = f.read()
            return ws_files

        workspace = cliargs[self.name]
        if self.is_workspace_volume(workspace):
            return generate_ws_files(workspace, only_ros_pacakges=True)
        else:
            # todo if rocker/docker supports ssh key passing in the build in the future, it would be better to use that inside a dockerfile
            # this is a workaround to check out the repos locally and copy them include them in the build context

            # todo support workspace file when docker-py supports build kit for ssh agent forwarding
            raise ValueError("Workspace file not currently supported")

            with tempfile.TemporaryDirectory() as td:
                workspace_file = cliargs[self.name]
                with open(workspace_file, "r") as f:
                    repos = yaml.safe_load(f)
                    for repo in repos:
                        vcs_type = list(repo.keys())[0]  # git, hg, svn, bzr
                        vc = VcsClient(
                            vcs_type, os.path.join(td, repo[vcs_type].get("local-name", ""))
                        )
                        vc.checkout(
                            repo[vcs_type]["uri"],
                            version=repo[vcs_type].get("version", ""),
                            shallow=True,
                        )

                return generate_ws_files(td)
    
    @staticmethod
    def get_rosdeps(workspace):
        if RosWs.is_workspace_volume(workspace):
            pass
        else:
            # todo support workspace file when docker-py supports build kit for ssh agent forwarding
            raise ValueError("Workspace file not currently supported")
            with open(workspace, "r") as f:
                repos = yaml.safe_load(f)

        # Get list of package.xml files
        package_xmls = []
        for root, dirs, files in os.walk(os.path.expanduser(workspace)):
            if 'package.xml' in files:
                package_xmls.append(os.path.join(root, 'package.xml'))

        # Parse package.xml files to get dependencies
        deps = set()
        src_packages = set()
        for package_xml in package_xmls:
            try:
                tree = ET.parse(package_xml)
                root = tree.getroot()

                src_packages.add(root.find('name').text.strip())
                
                # Get all depend, build_depend, run_depend, etc tags
                depend_tags = ['depend', 'build_depend', 'run_depend', 'exec_depend', 'test_depend']
                for tag in depend_tags:
                    for dep in root.findall(tag):
                        if dep.text:
                            dep_name = dep.text.strip()
                            deps.add(dep_name)
            except ET.ParseError:
                print(f"Warning: Could not parse {package_xml}")
                continue

        # skip source packages from dependencies
        return sorted(deps - src_packages)

    @staticmethod
    def get_home_dir(cliargs):
        if cliargs["user"]:
            return os.path.join(os.path.sep, "home", get_user_name())
        else:
            return os.path.join(os.path.sep, "root")

    def get_snippet(self, cliargs):
        args = {}
        args["home_dir"] = RosWs.get_home_dir(cliargs)
        args["rosdeps"] = RosWs.get_rosdeps(cliargs[self.name])
        args["install_deps"] = cliargs["ros_ws_install_deps"]

        snippet = pkgutil.get_data(
            "rocker",
            "templates/{}_snippet.Dockerfile.em".format(self.name),
        ).decode("utf-8")
        return em.expand(snippet, args)
    
    def get_user_snippet(self, cliargs):
        args = {}
        args["home_dir"] = RosWs.get_home_dir(cliargs)
        args["rosdeps"] = RosWs.get_rosdeps(cliargs[self.name])
        args["build_source"] = cliargs["ros_ws_build_source"]
        args["install_deps"] = cliargs["ros_ws_install_deps"]
        args["ros_master_uri"] = cliargs["ros_ws_ros_master_uri"]
        args["build_tool_args"] = cliargs["ros_ws_build_tool_args"]

        snippet = pkgutil.get_data(
            "rocker",
            "templates/{}_user_snippet.Dockerfile.em".format(self.name),
        ).decode("utf-8")

        print(em.expand(snippet, args))
        return em.expand(snippet, args)

    @staticmethod
    def register_arguments(parser, defaults={}):
        parser.add_argument(
            "--ros-ws",
            help="ROS workspace file. The workspace file is a yaml file that describes the ros workspace to be built in the container. It is expected that the desired $ROS_DISTRO is installed in the container and the environment variable is set (such is the case for the osrf/ros:<ros_distro>-desktop images)",
        )

        parser.add_argument(
            "--ros-ws-build-tool-args", nargs='+', default=[], help="Custom build tool args for catkin_tools (e.g. '--cmake-args -DCMAKE_BUILD_TYPE=RelWithDebInfo' '--install')"
        )

        parser.add_argument(
            "--ros-ws-install-deps",
            action="store_true",
            default=True,
            help="Install ROS dependencies based on package dependencies in the workspace",
        )

        parser.add_argument(
            "--ros-ws-build-source",
            action="store_true",
            default=True,
            help="Build the source of the ROS workspace",
        )

        parser.add_argument(
            "--ros-ws-ros-master-uri",
            help="Specifies a ROS Master URI to set in the bashrc",
        )
