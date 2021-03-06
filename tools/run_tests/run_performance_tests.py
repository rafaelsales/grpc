#!/usr/bin/env python2.7
# Copyright 2016, Google Inc.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are
# met:
#
#     * Redistributions of source code must retain the above copyright
# notice, this list of conditions and the following disclaimer.
#     * Redistributions in binary form must reproduce the above
# copyright notice, this list of conditions and the following disclaimer
# in the documentation and/or other materials provided with the
# distribution.
#     * Neither the name of Google Inc. nor the names of its
# contributors may be used to endorse or promote products derived from
# this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
# A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
# OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
# SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
# LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
# DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
# THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

"""Run performance tests locally or remotely."""

import argparse
import jobset
import multiprocessing
import os
import subprocess
import sys
import tempfile
import time
import uuid


_ROOT = os.path.abspath(os.path.join(os.path.dirname(sys.argv[0]), '../..'))
os.chdir(_ROOT)


_REMOTE_HOST_USERNAME = 'jenkins'


class CXXLanguage:

  def __init__(self):
    self.safename = 'cxx'

  def scenarios(self):
    # TODO(jtattermusch): add more scenarios
    return {
            # Scenario 1: generic async streaming ping-pong (contentionless latency)
            'cpp_async_generic_streaming_ping_pong': [
                '--rpc_type=STREAMING',
                '--client_type=ASYNC_CLIENT',
                '--server_type=ASYNC_GENERIC_SERVER',
                '--outstanding_rpcs_per_channel=1',
                '--client_channels=1',
                '--bbuf_req_size=0',
                '--bbuf_resp_size=0',
                '--async_client_threads=1',
                '--async_server_threads=1',
                '--secure_test=true',
                '--num_servers=1',
                '--num_clients=1',
                '--server_core_limit=0',
                '--client_core_limit=0'],
            # Scenario 5: Sync unary ping-pong with protobufs
            'cpp_sync_unary_ping_pong_protobuf': [
                '--rpc_type=UNARY',
                '--client_type=SYNC_CLIENT',
                '--server_type=SYNC_SERVER',
                '--outstanding_rpcs_per_channel=1',
                '--client_channels=1',
                '--simple_req_size=0',
                '--simple_resp_size=0',
                '--secure_test=true',
                '--num_servers=1',
                '--num_clients=1',
                '--server_core_limit=0',
                '--client_core_limit=0']}

  def __str__(self):
    return 'c++'


class CSharpLanguage:

  def __init__(self):
    self.safename = str(self)

  def __str__(self):
    return 'csharp'


class NodeLanguage:

  def __init__(self):
    pass
    self.safename = str(self)

  def __str__(self):
    return 'node'


_LANGUAGES = {
    'c++' : CXXLanguage(),
    'csharp' : CSharpLanguage(),
    'node' : NodeLanguage(),
}


class QpsWorkerJob:
  """Encapsulates a qps worker server job."""

  def __init__(self, spec, host_and_port):
    self._spec = spec
    self.host_and_port = host_and_port
    self._job = jobset.Job(spec, bin_hash=None, newline_on_success=True, travis=True, add_env={})

  def is_running(self):
    """Polls a job and returns True if given job is still running."""
    return self._job.state(jobset.NoCache()) == jobset._RUNNING

  def kill(self):
    return self._job.kill()


def create_qpsworker_job(language, port=10000, remote_host=None):
  # TODO: support more languages
  cmd = 'bins/opt/qps_worker --driver_port=%s' % port
  if remote_host:
    user_at_host = '%s@%s' % (_REMOTE_HOST_USERNAME, remote_host)
    cmd = 'ssh %s "cd ~/performance_workspace/grpc/ && %s"' % (user_at_host, cmd)
    host_and_port='%s:%s' % (remote_host, port)
  else:
    host_and_port='localhost:%s' % port

  jobspec = jobset.JobSpec(
      cmdline=[cmd],
      shortname='qps_worker',
      timeout_seconds=15*60,
      shell=True)
  return QpsWorkerJob(jobspec, host_and_port)


def create_scenario_jobspec(scenario_name, driver_args, workers, remote_host=None):
  """Runs one scenario using QPS driver."""
  # setting QPS_WORKERS env variable here makes sure it works with SSH too.
  cmd = 'QPS_WORKERS="%s" bins/opt/qps_driver ' % ','.join(workers)
  cmd += ' '.join(driver_args)
  if remote_host:
    user_at_host = '%s@%s' % (_REMOTE_HOST_USERNAME, remote_host)
    cmd = 'ssh %s "cd ~/performance_workspace/grpc/ && %s"' % (user_at_host, cmd)

  return jobset.JobSpec(
      cmdline=[cmd],
      shortname='qps_driver.%s' % scenario_name,
      timeout_seconds=3*60,
      shell=True,
      verbose_success=True)


def archive_repo():
  """Archives local version of repo including submodules."""
  # TODO: also archive grpc-go and grpc-java repos
  archive_job = jobset.JobSpec(
      cmdline=['tar', '-cf', '../grpc.tar', '../grpc/'],
      shortname='archive_repo',
      timeout_seconds=3*60)

  jobset.message('START', 'Archiving local repository.', do_newline=True)
  num_failures, _ = jobset.run(
      [archive_job], newline_on_success=True, maxjobs=1)
  if num_failures == 0:
    jobset.message('SUCCESS',
                   'Archive with local repository create successfully.',
                   do_newline=True)
  else:
    jobset.message('FAILED', 'Failed to archive local repository.',
                   do_newline=True)
    sys.exit(1)


def prepare_remote_hosts(hosts):
  """Prepares remote hosts."""
  prepare_jobs = []
  for host in hosts:
    user_at_host = '%s@%s' % (_REMOTE_HOST_USERNAME, host)
    prepare_jobs.append(
        jobset.JobSpec(
            cmdline=['tools/run_tests/performance/remote_host_prepare.sh'],
            shortname='remote_host_prepare.%s' % host,
            environ = {'USER_AT_HOST': user_at_host},
            timeout_seconds=3*60))
  jobset.message('START', 'Preparing remote hosts.', do_newline=True)
  num_failures, _ = jobset.run(
      prepare_jobs, newline_on_success=True, maxjobs=10)
  if num_failures == 0:
    jobset.message('SUCCESS',
                   'Remote hosts ready to start build.',
                   do_newline=True)
  else:
    jobset.message('FAILED', 'Failed to prepare remote hosts.',
                   do_newline=True)
    sys.exit(1)


def build_on_remote_hosts(hosts, build_local=False):
  """Builds performance worker on remote hosts."""
  build_timeout = 15*60
  build_jobs = []
  for host in hosts:
    user_at_host = '%s@%s' % (_REMOTE_HOST_USERNAME, host)
    build_jobs.append(
        jobset.JobSpec(
            cmdline=['tools/run_tests/performance/remote_host_build.sh'],
            shortname='remote_host_build.%s' % host,
            environ = {'USER_AT_HOST': user_at_host, 'CONFIG': 'opt'},
            timeout_seconds=build_timeout))
  if build_local:
    # Build locally as well
    build_jobs.append(
        jobset.JobSpec(
            cmdline=['tools/run_tests/performance/build_performance.sh'],
            shortname='local_build',
            environ = {'CONFIG': 'opt'},
            timeout_seconds=build_timeout))
  jobset.message('START', 'Building on remote hosts.', do_newline=True)
  num_failures, _ = jobset.run(
      build_jobs, newline_on_success=True, maxjobs=10)
  if num_failures == 0:
    jobset.message('SUCCESS',
                   'Build on remote hosts was successful.',
                   do_newline=True)
  else:
    jobset.message('FAILED', 'Failed to build on remote hosts.',
                   do_newline=True)
    sys.exit(1)


def start_qpsworkers(worker_hosts):
  """Starts QPS workers as background jobs."""
  if not worker_hosts:
    # run two workers locally
    workers=[(None, 10000), (None, 10010)]
  elif len(worker_hosts) == 1:
    # run two workers on the remote host
    workers=[(worker_hosts[0], 10000), (worker_hosts[0], 10010)]
  else:
    # run one worker per each remote host
    workers=[(worker_host, 10000) for worker_host in worker_hosts]

  return [create_qpsworker_job(CXXLanguage(),
                               port=worker[1],
                               remote_host=worker[0])
          for worker in workers]


def create_scenarios(languages, workers, remote_host=None):
  """Create jobspecs for scenarios to run."""
  scenarios = []
  for language in languages:
    for scenario_name, driver_args in language.scenarios().iteritems():
      scenario = create_scenario_jobspec(scenario_name,
                                         driver_args,
                                         workers,
                                         remote_host=remote_host)
      scenarios.append(scenario)

  # the very last scenario requests shutting down the workers.
  scenarios.append(create_scenario_jobspec('quit_workers',
                                           ['--quit=true'],
                                           workers,
                                           remote_host=remote_host))
  return scenarios


def finish_qps_workers(jobs):
  """Waits for given jobs to finish and eventually kills them."""
  retries = 0
  while any(job.is_running() for job in jobs):
    for job in qpsworker_jobs:
      if job.is_running():
        print 'QPS worker "%s" is still running.' % job.host_and_port
    if retries > 10:
      print 'Killing all QPS workers.'
      for job in jobs:
        job.kill()
    retries += 1
    time.sleep(3)
  print 'All QPS workers finished.'


argp = argparse.ArgumentParser(description='Run performance tests.')
argp.add_argument('--remote_driver_host',
                  default=None,
                  help='Run QPS driver on given host. By default, QPS driver is run locally.')
argp.add_argument('--remote_worker_host',
                  nargs='+',
                  default=[],
                  help='Worker hosts where to start QPS workers.')

args = argp.parse_args()

# Put together set of remote hosts where to run and build
remote_hosts = set()
if args.remote_worker_host:
  for host in args.remote_worker_host:
    remote_hosts.add(host)
if args.remote_driver_host:
  remote_hosts.add(args.remote_driver_host)

if remote_hosts:
  archive_repo()
  prepare_remote_hosts(remote_hosts)

build_local = False
if not args.remote_driver_host:
  build_local = True
build_on_remote_hosts(remote_hosts, build_local=build_local)

qpsworker_jobs = start_qpsworkers(args.remote_worker_host)

worker_addresses = [job.host_and_port for job in qpsworker_jobs]

try:
  scenarios = create_scenarios(languages=[CXXLanguage()],
                               workers=worker_addresses,
                               remote_host=args.remote_driver_host)
  if not scenarios:
    raise Exception('No scenarios to run')

  jobset.message('START', 'Running scenarios.', do_newline=True)
  num_failures, _ = jobset.run(
      scenarios, newline_on_success=True, maxjobs=1)
  if num_failures == 0:
    jobset.message('SUCCESS',
                   'All scenarios finished successfully.',
                   do_newline=True)
  else:
    jobset.message('FAILED', 'Some of the scenarios failed.',
                   do_newline=True)
    sys.exit(1)
finally:
  finish_qps_workers(qpsworker_jobs)
