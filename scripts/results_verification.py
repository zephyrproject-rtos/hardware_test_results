# Copyright (c) 2024 Nordic Semiconductor ASA
# SPDX-License-Identifier: Apache-2.0

import urllib.request
import json
from pathlib import Path
from argparse import ArgumentParser


def check_version_exists(zephyr=None):
    """
    Check if zephyr's version the PR targets exists in the list of daily versions

    :param zephyr: expected version of zephyr
    """
    ver_url = 'https://testing.zephyrproject.org/daily_tests/versions.json'
    try:
        webURL = urllib.request.urlopen(ver_url)
        data = webURL.read()
        ver_data = json.loads(data)
        for entry in reversed(ver_data):
            if zephyr == entry['version']:
                return True
        return False
    except TypeError:
        # Older entries in the daily list are not dictionaries but strings and going to far in the scan causes an error
        return False


def check_file_size(file_path, max_size=5):
    """
    Check if the file size of a given file is smaller than max_size.

    :param file_path: path of JSON report from twister to be checked
    :param max_size: max size allowed for the given file
    """
    file_size = file_path.stat().st_size / 1024 / 1024
    return bool(file_size <= max_size)


def check_name(platform_name=None, report=None):
    """
    Check if all testsuites in the report were executed on a given platform

    :param platform_name: name of the platform to match against
    :param report: dictionary with results
    """
    all_scanned = False
    err_cnt = 0
    all_clear = False
    for ts in report["testsuites"]:
        if ts["platform"] != platform_name:
            err_cnt += 1
            print(f"Platform {ts['platform']} from {ts['name']} doesn't match the required one: {platform_name}")
    else:
        all_scanned = True

    if all_scanned and err_cnt == 0:
        all_clear = True

    return all_clear


def check_attribute_value(report=None, item=None, max_value=None):
    """
    Check if the value of a given attribute is smaller than max_count. Attribute can be e.g. 'errors' or 'failures'

    :param report: dictionary with results
    :param item: attribute in summary to be verified
    :param max_value: max value of the given attribute
    """
    item_count = int(report["summary"][item])
    return bool(item_count <= max_value)


def check_version_consistent(report=None, version=None):
    """
    Check if zephyr's version given in the report match the expected one

    :param report: dictionary with results
    :param version: expected version of zephyr
    """
    if report['environment']['zephyr_version'] == version:
        return True

    print("Version not found.")
    return False


def parse_args():
    """Parse and return required limits from arguments"""
    argpar = ArgumentParser(description='Verifies if a given report fulfils requirements before its publishing')
    argpar.add_argument('-P', '--path', required=True, type=str, help='Path to the report which will be verified')
    argpar.add_argument('-Z', '--zephyr', required=True, type=str, help='Version of zephyr to be verified')
    argpar.add_argument('-S', '--max-size', default='5', type=float, help='Maximum size of a file that is accepted')
    """
    argpar.add_argument('-E', '--max-errors', default='50', type=int, help='Maximum accepted number'
                                                                           ' of errors in the report')
    argpar.add_argument('-F', '--max-failures', default='50', type=int, help='Maximum accepted number'
                                                                             ' of failures in the report')
    """
    return argpar.parse_args()


def main(args):
    file_path = Path(args.path)

    if not file_path.exists():
        print(f"JSON report not found at {file_path}")
        raise FileNotFoundError

    try:
        assert file_path.suffix == ".json", "Not a JSON file given"

        with open(file_path) as f:
            report = json.load(f)

        assert check_version_exists(args.zephyr), "Given version of zephyr is not on the daily list"
        assert check_name(file_path.stem, report), "Report name does not match the platform name given in the report"
        assert check_version_consistent(report, args.zephyr), "Incorrect version of zephyr"
        assert check_file_size(file_path, args.max_size) / 1024 / 1024, \
            f"Size of the JSON report at {file_path} is >{args.max_size} Mb"

        """
        # No summary available yet
        assert check_attribute_value(file_path, 'failures', args.max_failures), \
            f"JSON report at {file_path} has too many failures (>{args.max_failures}. It requires manual verification.)"
        assert check_attribute_value(file_path, 'errors', args.max_errors), \
            f"JSON report at {file_path} has too many errors (>{args.max_errors}. It requires manual verification.)"
        """
    except AssertionError as ex:
        print(ex)
        exit(1)

    print(f"Report {args.path} verified.")
    return 0


if __name__ == "__main__":
    args = parse_args()
    main(args)
