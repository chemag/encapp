#!/usr/local/bin/python3

"""Python script to run ENCAPP tests on Android and collect results.
The script will create a directory based on device model and date,
and save encoded video and rate distortion results in the directory
"""

import os
import subprocess
import json
import sys
import argparse
import re
import time

from datetime import datetime
from os.path import exists


ACTIVITY = 'com.facebook.encapp/.MainActivity'
ENCAPP_OUTPUT_FILE_NAME_RE = r'encapp_.*'
RD_RESULT_FILE_NAME = 'rd_results.json'

SCRIPT_PATH = os.path.realpath(__file__)
SCRIPT_DIR, _ = os.path.split(SCRIPT_PATH)
MAIN_APK = os.path.join(
    SCRIPT_DIR,
    '/../app/build/outputs/apk/debug/com.facebook.encapp-v1.0-debug.apk')

FUNC_CHOICES = {
    'help': 'show help options',
    'install': 'install apks',
    'list': 'list codecs and devices supported',
    'encode': 'run test case(s)',
}

default_values = {
    'debug': 0,
    'func': 'help',
    'install': False,
    'infile': None,
    'output': None,
}


def run_cmd(cmd, silent=False):
    ret = True
    try:
        if not silent:
            print(cmd, sep=' ')
        process = subprocess.Popen(cmd, shell=True,
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        stdout, stderr = process.communicate()
    except Exception:
        ret = False
        print('Failed to run command: ' + cmd)

    return ret, stdout.decode(), stderr.decode()


# returns info (device model and serial number) about the device where the
# test will be run
def get_device_info(serial_inp, debug=0):
    # list all available devices
    adb_cmd = 'adb devices -l'
    ret, stdout, stderr = run_cmd(adb_cmd)
    assert ret, 'error: failed to get adb devices'

    # parse list
    device_info = {}
    for line in stdout.split('\n'):
        if line == 'List of devices attached' or line == '':
            continue
        serial = line.split()[0]
        item_dict = {}
        for item in line.split()[1:]:
            # ':' used to separate key/values
            if ':' in item:
                key, val = item.split(':', 1)
                item_dict[key] = val
        # ensure the 'model' field exists
        if 'model' not in item_dict:
            item_dict['model'] = 'generic'
        device_info[serial] = item_dict
    assert len(device_info) > 0, 'error: no devices connected'
    if debug > 2:
        print('available devices: %r' % device_info)

    # select output device
    serial, model = None, None
    if serial_inp is None:
        # if user did not select a serial_inp, make sure there is only one
        # device available
        assert len(device_info) == 1, (
            'error: need to choose a device %r' % list(device_info.keys()))
        serial = list(device_info.keys())[0]
        model = device_info[serial]

    else:
        # if user forced a serial number, make sure it is available
        assert serial_inp in device_info, (
            'error: device %s not available' % serial_inp)
        serial = serial_inp
        model = device_info[serial]

    if debug > 0:
        print('selecting device: serial: %s model: %s' % (serial, model))

    # remove any files that are generated in previous runs
    adb_cmd = 'adb -s ' + serial + ' shell ls /sdcard/'
    ret, stdout, stderr = run_cmd(adb_cmd)
    output_files = re.findall(ENCAPP_OUTPUT_FILE_NAME_RE, stdout, re.MULTILINE)
    for file in output_files:
        if file == '':
            continue
        # remove the output
        adb_cmd = 'adb -s ' + serial + ' shell rm /sdcard/' + file
        run_cmd(adb_cmd)

    return model, serial


def wait_for_exit(serial):
    adb_cmd = f'adb -s {serial} shell pidof com.facebook.encapp'
    pid = -1
    current = 1
    while (current != -1):
        if pid == -1:
            ret, stdout, stderr = run_cmd(adb_cmd, silent=True)
            pid = -1
            if len(stdout) > 0:
                pid = int(stdout)
        time.sleep(1)
        ret, stdout, stderr = run_cmd(adb_cmd, silent=True)
        current = -2
        if len(stdout) > 0:
            current = int(stdout)
        else:
            current = -1
    print(f'Exit from {pid}')


def install_app(serial):
    run_cmd(f'adb -s {serial} install -g {MAIN_APK}', silent=True)


def run_test(workdir, json_path, json_name,
             input_files, result_json, serial, options):
    run_cmd(f'adb -s {serial} push {json_name} /sdcard/', silent=True)

    additional = ''
    if options.codec is not None and len(options.codec) > 0:
        additional = f'{additional} -e enc {options.codec}'

    if options.input is not None and len(options.input) > 0:
        additional = f'{additional} -e file {options.input}'

    if options is not None and len(options.input_res) > 0:
        additional = f'{additional} -e ref_res {options.input_res}'

    if options is not None and len(options.input_fps) > 0:
        additional = f'{additional} -e ref_fps {options.input_fps}'

    if options is not None and len(options.output_fps) > 0:
        additional = f'{additional} -e fps {options.output_fps}'

    if options is not None and len(options.output_res) > 0:
        additional = f'{additional} -e res {options.output_res}'

    run_cmd(f'adb -s {serial} shell am start -W {additional} -e test '
            f'/sdcard/{json_name} {ACTIVITY}')
    wait_for_exit(serial)
    adb_cmd = 'adb -s ' + serial + ' shell ls /sdcard/'
    ret, stdout, stderr = run_cmd(adb_cmd, silent=True)
    output_files = re.findall(ENCAPP_OUTPUT_FILE_NAME_RE, stdout,
                              re.MULTILINE)

    base_file_name = os.path.basename(json_path).rsplit('.', 1)[0]
    sub_dir = '_'.join([base_file_name, 'files'])
    output_dir = f'{workdir}/{sub_dir}/'
    run_cmd(f'mkdir {output_dir}')

    for file in output_files:
        if file == '':
            print('No file found')
            continue
        # pull the output file
        print(f'pull {file} to {output_dir}')

        adb_cmd = f'adb -s {serial} pull /sdcard/{file} {output_dir}'
        run_cmd(adb_cmd, silent=True)

        # remove the json file on the device too
        adb_cmd = f'adb -s {serial} shell rm /sdcard/{file}'
        run_cmd(adb_cmd, silent=True)
        if file.endswith('.json'):
            path, tmpname = os.path.split(file)
            result_json.append(f'{output_dir}/{tmpname}')

    adb_cmd = f'adb -s {serial} shell rm /sdcard/{json_name}'
    run_cmd(adb_cmd)
    return result_json


def run_encode_tests(test_def, json_path, model, serial, test_desc,
                     workdir, options):
    result_json = []
    if options.no_install is not None and options.no_install:
        print('Skip install of apk!')
    else:
        install_app(serial)

    if test_def is None:
        raise Exception('No test files')

    path, filename = os.path.split(json_path)
    # remove old encapp files on device (!)
    run_cmd(f'adb -s {serial} rm /sdcard/encapp_*')
    # run_cmd(f'adb -s {serial} push {json_path} /sdcard/')

    json_folder = os.path.dirname(json_path)
    inputfile = ''
    tests = test_def.get('tests')
    print(f'tests {tests}')
    if isinstance(tests, type(None)):
        tests = [test_def]
    counter = 1
    all_input_files = []
    # push media files to the device
    for test in tests:
        print(f'push data for test = {test}')
        if options is not None and len(options.input) > 0:
            all_input_files.append(inputfile)
            inputfile = f'/sdcard/{os.path.basename(options.input)}'
            ret, stdout, stderr = run_cmd(
                f'adb -s {serial} shell ls {inputfile}', silent=True)
            if len(stderr) > 0:
                run_cmd(f'adb -s {serial} push {options.input} '
                        '/sdcard/', silent=True)
        else:
            input_files = test.get('input_files')
            if input_files is not None:
                for file in input_files:
                    if len(json_folder) > 0 and not os.path.isabs(file):
                        path = f'{json_folder}/{file}'
                    else:
                        path = f'{file}'
                    all_input_files.append(f'/sdcard/{os.path.basename(path)}')
                    if exists(path):
                        run_cmd(f'adb -s {serial} push {path} /sdcard/')
                    else:
                        print(f'Media file is missing: {path}')
                        exit(0)

    # run test(s)
    if not options.no_split:
        for test in tests:
            json_name = f'{filename}_{counter}.json'
            counter += 1
            with open(json_name, "w") as outfile:
                json.dump(test, outfile)
            run_test(workdir, json_path, json_name, input_files,
                     result_json, serial, options)
            os.remove(json_name)
    else:
        run_test(workdir, json_folder, filename, input_files,
                 result_json, serial, options)

    if len(all_input_files) > 0:
        for file in all_input_files:
            run_cmd(f'adb -s {serial} shell rm {file}')

    return result_json


def list_codecs(serial, model):

    adb_cmd = f'adb -s {serial} shell am start '\
              f'-e ui_hold_sec 3 '\
              f'-e list_codecs a {ACTIVITY}'

    run_cmd(adb_cmd, silent=True)
    wait_for_exit(serial)
    filename = f'codecs_{model}.txt'
    adb_cmd = f'adb -s {serial} pull /sdcard/codecs.txt {filename}'
    run_cmd(adb_cmd)
    with open(filename, 'r') as codec_file:
        lines = codec_file.readlines()
        for line in lines:
            print(line.split('\n')[0])
        print(f'File is available in current dir as {filename}')


def convert_config(input_config, options):
    test_config = ''
    return test_config


def encode_test(options, model, serial):
    # get date and time and format it
    now = datetime.now()
    dt_string = now.strftime('%Y-%m-%d_%H_%M')

    # get working directory directory
    if options.output is not None:
        workdir = options.output
    else:
        workdir = f'{options.desc.replace(" ", "_")}_{model}_{dt_string}'
    os.system('mkdir -p ' + workdir)

    # read input file
    with open(options.infile, 'r') as fp:
        if options.debug > 0:
            print(f"infile: {options.infile} install: {options.install}")
        input_config = json.load(fp)

    # convert the human-friendly input into a valid apk input
    test_config = convert_config(input_config, options)

    # run the encode test
    run_encode_tests(input_config,
                     options.infile,
                     model,
                     serial,
                     options.desc if options.desc is not None else '',
                     workdir,
                     options)


def get_options(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
            '-d', '--debug', action='count',
            dest='debug', default=default_values['debug'],
            help='Increase verbosity (use multiple times for more)',)
    parser.add_argument(
            '--quiet', action='store_const',
            dest='debug', const=-1,
            help='Zero verbosity',)
    parser.add_argument('--serial', help='Android device serial number')
    parser.add_argument(
            '--install', action='store_const',
            dest='install', const=True,
            default=default_values['install'],
            help='Do install apk',)
    parser.add_argument(
            '--no-install', action='store_const',
            dest='install', const=False,
            help='Do not install apk',)
    parser.add_argument(
            'func', type=str, nargs='?',
            default=default_values['func'],
            choices=FUNC_CHOICES.keys(),
            metavar='%s' % (' | '.join("{}: {}".format(k, v) for k, v in
                                       FUNC_CHOICES.items())),
            help='function arg',)
    parser.add_argument(
            'infile', type=str, nargs='?',
            default=default_values['infile'],
            metavar='input-file',
            help='input file',)
    parser.add_argument(
            'output', type=str, nargs='?',
            default=default_values['output'],
            metavar='output',
            help='output dir or file',)

    options = parser.parse_args(argv[1:])

    # implement help
    if options.func == 'help':
        parser.print_help()
        sys.exit(0)

    if options.serial is None and 'ANDROID_SERIAL' in os.environ:
        # read serial number from ANDROID_SERIAL env variable
        options.serial = os.environ['ANDROID_SERIAL']

    return options


def main(argv):
    options = get_options(argv)

    # get serial number
    model, serial = get_device_info(options.serial, options.debug)
    if type(model) is dict:
        if 'model' in model:
            model = model.get('model')
        else:
            model = list(model.values())[0]
    if options.debug > 0:
        print(f'model = {model}')

    # install app
    if options.func == 'install' or options.install:
        install_app(serial)

    # run function
    if options.func == 'list':
        list_codecs(serial)

    elif options.func == 'encode':
        # ensure there is an input configuration
        assert options.infile is not None, 'error: need a valid input file'
        encode_test(options, model)


if __name__ == '__main__':
    main(sys.argv)