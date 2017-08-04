#!/usr/bin/env python
#
# Copyright (C) 2016 Red Hat
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.
# -*- coding: utf-8 -*-

import argparse
import json
import pexpect
import pipes
import os
import random
import re
import subprocess
import sys
import time
import yaml


# TODO should be settable
READING_TIME = 2
TYPING_SPEED = 0.08


def pause(t):
    time.sleep(t)


def is_process_running_in_tmux(session):
    """Find out whether there is a process currently running or not in tmux"""
    # since processes are launched as bash -c 'xx yy zz' the only pid
    # we can get at first is the one of the bash process.
    cmd = 'tmux list-panes -F #{pane_pid} -t %s' % session
    father = subprocess.Popen(cmd.split(' '), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE)
    father = father.stdout.readlines()[0].strip('\n')
    # then we just check whether this process still has children:
    cmd = 'pgrep -P %s' % father
    child = subprocess.Popen(cmd.split(' '), stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
    child = child.stdout.readlines()
    if child:
        return child[0].strip('\n')
    return False

def is_tmux_session_attached(session):
    """Find out whether or not a tmux session is attached"""
    cmd = ['tmux', 'list-clients', '-t', session]
    out = subprocess.check_output(cmd)
    return bool(out.strip())


def popen(cmd):
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

class TmuxWrapper:
    def __init__(self, session):
        self.session = session

    def _get_buffer(self):
        capture_cmd = ['tmux', 'capture-pane', '-S', '-', '-t', self.session]
        subprocess.check_call(capture_cmd)
        buf = subprocess.check_output(['tmux', 'show-buffer'])
        return buf

    def status_off(self):
        cmd = ['tmux', 'set-option', '-t', self.session, '-g', 'status', 'off']
        subprocess.check_call(cmd)

    def send_keys(self, keys):
        cmd = ["tmux", "send-keys", "-t", self.session, keys]
        subprocess.check_call(cmd)

    def emulate_typing(self, line, speed=TYPING_SPEED):
        for char in line:
            if char == ' ':
                char = 'Space'
            self.send_keys(char)
            if speed:
                pause(random.randrange(0,50)/400.0)

    def send_enter(self):
        self.send_keys('C-m')

    def press_key(self, key):
        key_mapping = {
            'ENTER': 'C-m',
            'BACKSPACE': 'C-h'
        }
        key = key_mapping.get(key.upper(), 'C-m')
        self.send_keys(key)

    def type(self, line):
        self.emulate_typing(line, speed=0)
        self.send_enter()

    def dialog(self, line, hesitate=0):
        self.emulate_typing('# ' + line)
        self.pause(hesitate)
        self.send_enter()


    def action(self, action, hesitate=0, wait=False, keep=None, vars={}):
        original_buffer = self._get_buffer().strip('\n')
        to_keep = {}
        if keep:
            to_keep = dict((u['var'],
                             re.compile(u['regex'], re.M)) for u in keep)

        cmd = action
        # replace vars if needed
        for var, value in vars.items():
            if var in self.cmd:
                cmd = cmd.replace(var, value)

        self.emulate_typing(cmd)
        self.pause(hesitate)
        self.send_enter()
        if wait:
            while is_process_running_in_tmux(self.session):
                pause(0.1)
        # output will be after the sent command. Remove old buffer first:
        b = self._get_buffer()[len(original_buffer):]
        output = b.split(cmd)[-1].strip('\n')
        for var, regex in to_keep.items():
            match = regex.findall(output)
            if match:
                # TODO support multiple outputs
                vars[var] = match[0]

    def pause(self, pause=1):
        time.sleep(float(pause))

class Movie:
    def __init__(self, name, script, output_file):
        self.script = script
        self.session_name = name
        self.output_file = output_file
        self.reel = None
        self.vars = {}

        self.terminal = TmuxWrapper(name)

    def process_scene(self, scene):
        if 'action' in scene:
            self.terminal.action(vars=self.vars, **scene)
        elif 'line' in scene:
            self.terminal.dialog(**scene)
        elif 'press_key' in scene:
            self.terminal.press_key(**scene)
        elif 'pause' in scene:
            self.terminal.pause(**scene)
        else:
            sys.exit(1)

    def shoot(self):
        """shoot the movie."""
        self.reel = popen(['tmux', 'new-session', '-d', '-s', self.session_name])
        pause(0.5)
        self.terminal.status_off()
        # start filming
        asciinema_cmd = 'asciinema rec -c "tmux attach -t %s" -y'
        if self.script.get('title'):
            asciinema_cmd += ' -t %s' % pipes.quote(self.script.get('title'))
        asciinema_cmd += ' %s'
        full_asciinema_cmd = asciinema_cmd % (self.session_name, os.path.abspath(self.output_file))
        if 'before' in self.script:
            self.terminal.type(self.script['before'])
            
        print "Run to record:"
        print full_asciinema_cmd
        while not is_tmux_session_attached(self.session_name):
            print "Waiting for you to attach to the tmux session"
            pause(2)
        for scene in self.script['scenes']:
            name = scene.pop('name')
            sys.stdout.write('Rolling scene "%s"...' % name)
            sys.stdout.flush()
            self.process_scene(scene)
            print " Cut !"
        self.terminal.send_keys('exit')
        self.terminal.send_keys('C-m')
        self.reel.communicate('exit')
        return '', ''


def trim_movie(j):
    """Remove the 'exit' and everything after it in the movie"""

    #Find the last frame that looks like
    #[
    #  5.016473,
    #  "exit"
    #],
    #This would be more efficient from the end, but whatever
    frames = j['stdout']
    last_exit = None
    for idx, f in enumerate(frames):
        if f[1] == "exit":
            last_exit = idx

    if last_exit is None:
        return j

    j['stdout'] = frames[:last_exit]
    return j

def main():
    parser = argparse.ArgumentParser(description="spielbash CLI")
    parser.add_argument('--script', metavar='RaidersOfTheLostArk.yaml',
                        help='The script to execute with asciinema',
                        required=True)
    parser.add_argument('--output', metavar='RaidersOfTheLostArk.json',
                        help='where to record the movie',
                        required=False, default='movie.json')
    args = parser.parse_args()
    script_file = args.script
    output_file = args.output
    try:
        with open(script_file, 'r') as s:
            script = yaml.load(s)
    except Exception as e:
        sys.exit('There was a problem with loading the script: %s' % e)
    movie = Movie('howdy', script, output_file)
    # CAMERAS, LIGHTS AAAAAAAND ACTION !
    out, err = movie.shoot()
    if err:
        print err
    else:
        # Pretty print json and remove extra crap at the end
        print ("Giving asciinema 2 seconds to exit..")
        pause(2)
        with open(output_file, 'r') as m:
            j = json.load(m)
        j = trim_movie(j)
        with open(output_file, 'w') as m:
            json.dump(j, m)
        print "movie recorded as %s" % output_file
        print "to replay: asciinema play %s" % output_file
        print "to upload: asciinema upload %s" % output_file


if __name__ == '__main__':
    main()
