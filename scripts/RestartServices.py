import argparse
import logging
import os
import socket
import subprocess

from yaml import load

try:
    from yaml import CLoader as Loader, CDumper as Dumper
except ImportError:
    from yaml import Loader, Dumper
from wwpdb.utils.config.ConfigInfo import ConfigInfo, getSiteId

logger = logging.getLogger()


def get_config_info_variable(siteId, variable):
    variable = variable.upper()
    cI = ConfigInfo(siteId)
    return cI.get(variable)


class ServerMaintenance:

    def __init__(self, siteId):
        self.siteId = siteId
        self.site_config_path = get_config_info_variable(siteId=self.siteId, variable='top_wwpdb_site_config_dir')
        self.site_suffix = get_config_info_variable(siteId=self.siteId, variable='site_suffix')

    def get_service_yaml(self):
        yaml_path = os.path.join(self.site_config_path, 'ansible', self.site_suffix + '.yml')
        return yaml_path

    def get_command_yaml(self):
        yaml_path = os.path.join(self.site_config_path, 'ansible', 'playbook.yml')
        return yaml_path

    def parse_yaml(self, yaml_path):
        if os.path.exists(yaml_path):
            with open(yaml_path) as in_file:
                return load(in_file, Loader=Loader)
        return {}

    def get_service_servers(self, service):
        yaml_file = self.get_service_yaml()
        yaml_data = self.parse_yaml(yaml_path=yaml_file)
        return yaml_data.get('all', {}).get('children', {}).get(service, {}).get('hosts', {}).keys()

    def get_service_commands(self):
        yaml_file = self.get_command_yaml()
        yaml_data = self.parse_yaml(yaml_path=yaml_file)
        ret_data = {}
        for row in yaml_data:
            print(row)
            service = row.get('hosts')
            print(service)
            tasks = row.get('tasks', [])
            for task in tasks:
                command = task.get('shell')
                print(command)
                ret_data.setdefault(service, []).append(command)

        return ret_data

    def get_servers(self, service):
        servers_from_yaml = self.get_service_servers(service=service)
        logging.info('servers from yaml: {}'.format(servers_from_yaml))
        return servers_from_yaml if servers_from_yaml else [socket.gethostname()]

    def get_apache_servers(self):
        return self.get_servers(service='apache')

    def get_wfe_servers(self):
        return self.get_servers(service='wfe')

    def get_val_api_servers(self):
        return self.get_servers(service='val_api_consumers')

    def get_val_rel_servers(self):
        return self.get_servers(service='val_rel_consumers')

    def get_api_servers(self):
        return self.get_servers(service='api_consumers')

    def get_stop_apache_command(self):
        return 'killall httpd; sleep 2s; killall -9 httpd; sleep 2s; killall -9 httpd; echo $HOST'

    def get_apache_command(self):
        return os.path.join(get_config_info_variable(siteId=self.siteId, variable='TOP_WWPDB_SITE_CONFIG_DIR'),
                            'apache_config', 'httpd-opt')

    def get_restart_apache(self):
        restart_apache_command = '{}; {} start'.format(self.get_stop_apache_command(), self.get_apache_command())
        logging.debug(restart_apache_command)
        return restart_apache_command

    def get_root_wf_engine_command(self):
        return 'python -m wwpdb.apps.wf_engine.wf_engine_utils.run.WFEngineRunnerExec'

    def get_rmq_consumer_command(self):
        return 'python -m wwpdb.apps.content_ws_server.service.ContentRequestServiceHandler'

    def get_val_rel_consumer_command(self):
        return 'python -m wwpdb.apps.val_rel.service.ValidationReleaseServiceHandler'

    def get_val_api_consumer_command(self):
        return 'python -m wwpdb.apps.val_ws_server.service.ValidationServiceHandler'

    def start_command(self, command):
        return '{} --start'.format(command)

    def stop_command(self, command):
        return '{} --stop'.format(command)

    def status_command(self, command):
        return '{} --status'.format(command)

    def restart_command(self, command):
        return '{} --restart'.format(command)

    def get_restart_wf_command(self):
        restart_wfe_command = 'source $VENV_PATH/bin/activate; {}; sleep 2s; {}'.format(
            self.stop_command(self.get_root_wf_engine_command()), self.start_command(self.get_root_wf_engine_command()))
        logging.info(restart_wfe_command)
        return restart_wfe_command

    def get_start_wf_command(self):
        restart_wfe_command = 'source $VENV_PATH/bin/activate; {}'.format(
            self.start_command(self.get_root_wf_engine_command()))
        logging.info(restart_wfe_command)
        return restart_wfe_command

    def run_command_via_ssh(self, server, command):
        # ssh_command = 'ssh {} "{}"'.format(server, command)
        ssh_command = '{}'.format(command)
        logging.info(server)
        logging.info(command)
        # subprocess.check_call(ssh_command, shell=True)
        subprocess.call(ssh_command, shell=True)

    def run_command_on_servers(self, servers, command):
        for server in servers:
            self.run_command_via_ssh(server=server, command=command)

    def restart_apache_on_servers(self):
        self.run_command_on_servers(servers=self.get_apache_servers(), command=self.get_restart_apache())

    def stop_apache_on_servers(self):
        self.run_command_on_servers(servers=self.get_apache_servers(), command=self.get_stop_apache_command())

    def restart_wfe_on_servers(self):
        self.run_command_on_servers(servers=self.get_wfe_servers(), command=self.get_restart_wf_command())

    def start_wfe_on_servers(self):
        self.run_command_on_servers(servers=self.get_wfe_servers(), command=self.get_start_wf_command())

    def stop_wfe_on_servers(self):
        self.run_command_on_servers(servers=self.get_wfe_servers(),
                                    command=self.stop_command(self.get_root_wf_engine_command()))

    def status_of_wfe_on_servers(self):
        self.run_command_on_servers(servers=self.get_wfe_servers(),
                                    command=self.status_command(self.get_root_wf_engine_command()))

    def stop_api_consumers_on_servers(self):
        self.run_command_on_servers(servers=self.get_api_servers(),
                                    command=self.stop_command(self.get_rmq_consumer_command()))

    def restart_api_consumers_on_servers(self):
        self.run_command_on_servers(servers=self.get_api_servers(),
                                    command=self.restart_command(self.get_rmq_consumer_command()))

    def status_of_api_consumers_on_servers(self):
        self.run_command_on_servers(servers=self.get_api_servers(),
                                    command=self.status_command(self.get_rmq_consumer_command()))

    def start_api_consumers_on_servers(self):
        self.run_command_on_servers(servers=self.get_api_servers(),
                                    command=self.start_command(self.get_rmq_consumer_command()))

    def get_instance_command(self, command, instance):
        return "{0} --instance {1}; echo `hostname` ;echo instance {1}".format(command, instance)

    def run_command_per_instance(self, server, command, num_workers):
        command_list = []
        if num_workers:
            for x in range(1, num_workers + 1):
                command_list.append(self.get_instance_command(command, x))
        else:
            command_list.append(command)
        for command in command_list:
            self.run_command_via_ssh(server=server, command=command)

    def stop_val_rel_consumers_on_servers(self, num_workers=0):
        command = self.stop_command(self.get_val_rel_consumer_command())
        for server in self.get_val_rel_servers():
            self.run_command_per_instance(server=server, command=command, num_workers=num_workers)

    def start_val_rel_consumers_on_servers(self, num_workers=0):
        command = self.start_command(self.get_val_rel_consumer_command())
        for server in self.get_val_rel_servers():
            self.run_command_per_instance(server=server, command=command, num_workers=num_workers)

    def restart_val_rel_consumers_on_servers(self, num_workers=0):
        command = self.restart_command(self.get_val_rel_consumer_command())
        for server in self.get_val_rel_servers():
            self.run_command_per_instance(server=server, command=command, num_workers=num_workers)

    def stop_val_api_consumers_on_servers(self, num_workers=0):
        command = self.stop_command(self.get_val_api_consumer_command())
        for server in self.get_val_api_servers():
            self.run_command_per_instance(server=server, command=command, num_workers=num_workers)

    def start_val_api_consumers_on_servers(self, num_workers=0):
        command = self.start_command(self.get_val_api_consumer_command())
        for server in self.get_val_api_servers():
            self.run_command_per_instance(server=server, command=command, num_workers=num_workers)

    def restart_val_api_consumers_on_servers(self, num_workers=0):
        command = self.restart_command(self.get_val_api_consumer_command())
        for server in self.get_val_api_servers():
            self.run_command_per_instance(server=server, command=command, num_workers=num_workers)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-d', '--debug', help='debugging', action='store_const', dest='loglevel',
                        const=logging.DEBUG,
                        default=logging.INFO)
    parser.add_argument('--restart_wfe', help='restart the WorkFlow Engines', action='store_true')
    parser.add_argument('--start_wfe', help='start the WorkFlow Engines', action='store_true')
    parser.add_argument('--status_of_wfe', help='status of the WorkFlow Engines', action='store_true')
    parser.add_argument('--stop_wfe', help='stop the WorkFlow Engines', action='store_true')
    parser.add_argument('--restart_apache', help='restart the Apaches', action='store_true')
    parser.add_argument('--stop_apache', help='stop the Apaches', action='store_true')
    parser.add_argument('--start_api_consumers', help='start the OneDep API consumers', action='store_true')
    parser.add_argument('--status_of_api_consumers', help='status of the OneDep API consumers', action='store_true')
    parser.add_argument('--restart_api_consumers', help='restart the OneDep API consumers', action='store_true')
    parser.add_argument('--stop_api_consumers', help='stop the OneDep API consumers', action='store_true')
    parser.add_argument('--start_val_rel_consumers',
                        help='start the OneDep release validation report generation consumers', type=int, default=0)
    parser.add_argument('--restart_val_rel_consumers',
                        help='restart the OneDep release validation report generation consumers', type=int, default=0)
    parser.add_argument('--stop_val_rel_consumers',
                        help='stop the OneDep release valdiation report generation consumers', type=int, default=0)
    parser.add_argument('--start_val_api_consumers', help='start the OneDep release validation API consumers', type=int,
                        default=0)
    parser.add_argument('--restart_val_api_consumers', help='restart the OneDep release validation API consumers',
                        type=int, default=0)
    parser.add_argument('--stop_val_api_consumers', help='stop the OneDep release valdiation report API consumers',
                        type=int, default=0)
    parser.add_argument('--siteID', help='siteID to run the update on', type=str, default=None)
    args = parser.parse_args()
    logger.setLevel(args.loglevel)

    siteId = args.siteID
    if not siteId:
        siteId = getSiteId()

    sm = ServerMaintenance(siteId=siteId)

    if args.stop_apache:
        sm.stop_apache_on_servers()
    if args.restart_apache:
        sm.restart_apache_on_servers()
    if args.stop_wfe:
        sm.stop_wfe_on_servers()
    if args.restart_wfe:
        sm.restart_wfe_on_servers()
    if args.start_wfe:
        sm.start_wfe_on_servers()
    if args.status_of_wfe:
        sm.status_of_wfe_on_servers()
    if args.start_api_consumers:
        sm.start_api_consumers_on_servers()
    if args.status_of_api_consumers:
        sm.status_of_api_consumers_on_servers()
    if args.restart_api_consumers:
        sm.restart_api_consumers_on_servers()
    if args.stop_api_consumers:
        sm.stop_api_consumers_on_servers()
    if args.start_val_rel_consumers:
        sm.start_val_rel_consumers_on_servers(num_workers=args.start_val_rel_consumers)
    if args.restart_val_rel_consumers:
        sm.restart_val_rel_consumers_on_servers(num_workers=args.restart_val_rel_consumers)
    if args.stop_val_rel_consumers:
        sm.stop_val_rel_consumers_on_servers(num_workers=args.stop_val_rel_consumers)
    if args.start_val_api_consumers:
        sm.start_val_api_consumers_on_servers(num_workers=args.start_val_api_consumers)
    if args.restart_val_api_consumers:
        sm.restart_val_api_consumers_on_servers(num_workers=args.restart_val_api_consumers)
    if args.stop_val_api_consumers:
        sm.stop_val_api_consumers_on_servers(num_workers=args.stop_val_api_consumers)


if '__main__' in __name__:
    main()
