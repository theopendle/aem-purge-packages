import re
from pathlib import Path
import requests
import argparse
from datetime import datetime

# Declare constants
TMP_DIRECTORY = '/tmp/package-purge'
TMP_RETAIN = TMP_DIRECTORY + '/retain.txt'
TMP_REMOVE = TMP_DIRECTORY + '/remove.txt'
SIZE_COEFFICIENTS = dict({
    'KB': 0.000001,
    'MB': 0.001,
    'GB': 1,
})
ARGUMENT_DEFINITIONS = {
    "date": {"test": re.compile(r'\d{4}-\d{2}-\d{2}'), "default": datetime.today().strftime('%Y-%m-%d')},
    "path": {"test": re.compile(r"[\w.-]/?"), "default": ""},
    "host": {"test": re.compile(r"\w+(:\d+)?"), "default": "localhost:4502"},
    "user": {"test": re.compile(r".*:.*"), "default": "admin:admin"}
}


def main():
    args = read_arguments()
    print(args)
    validate_arguments(args)

    # Set arguments or defaults
    date = set_argument("date", args)
    path = set_argument("path", args)
    host = set_argument("host", args)
    credentials = tuple(set_argument("user", args).split(':'))

    # Create tmp directory
    Path(TMP_DIRECTORY).mkdir(parents=True, exist_ok=True)

    result = get_packages(host, credentials, path, date, args.verbose)
    print("{0} packages found".format(result["total"]))

    packages = result["packages"]

    conventional_packages = [package for package in packages if is_conventional(package)]

    best_packages = determine_best_packages(conventional_packages)

    outdated_packages = [package for package in conventional_packages if package not in best_packages]
    print("{0} outdated packages found".format(len(outdated_packages)))
    if len(outdated_packages) is 0:
        exit(0)

    outdated_snapshots = find_outdated_snapshots(packages, outdated_packages)
    all_outdated = outdated_packages + outdated_snapshots

    total_size = calculate_size(get_packages(host, credentials, '',
                                             datetime.today().strftime('%Y-%m-%d'), args.verbose)["packages"])
    size_to_remove = calculate_size(all_outdated)
    print_size(size_to_remove, total_size)

    confirm()
    print("Purging packages...")

    purge_packages(outdated_packages, host, credentials, args.verbose, args.force)


def get_packages(host, credentials, path, date, verbose):
    """
    Queries AEM for a list of packages
    """
    print("Getting package list from AEM ...")
    try:
        response = requests.get(
            'http://{0}/bin/querybuilder.json?path=/etc/packages/{1}'
            '&type=nt:file&p.limit=-1&daterange.property=jcr:created&daterange.upperBound={2}'.format(
                host, path, date),
            auth=credentials)
        if response.status_code == 200:
            data = response.json()
            print("Done" if data["success"] else "Failed")
            if verbose:
                print(data)

            return {
                "total": data["results"],
                "packages": [{"path": hit["path"], "size": hit["size"]} for hit in data["hits"]]
            }
        else:
            print("Failed: {0}".format(re))
            exit(1)
    except Exception as e:
        print("Could not reach AEM")
        if verbose:
            print(e)
        exit(1)


def is_conventional(package):
    """
    Determines if a package:
     - follows the conventional naming and version format
     - is a real package and not a package snapshot
    """
    path = package["path"]
    if '.snapshot' in path:
        return False

    regex = re.compile(r'(^.*-)(\d{1,3}(\.\d{1,3})?(.\d{1,4})?)(\.zip)$')
    parts = re.search(regex, path)
    return parts is not None and len(parts.groups()) == 5


def calculate_size(packages):
    """
    Caclulates the total size in GB of the list of packages provided.
    """
    total = 0.0
    regex = re.compile(r'(\d+)(\s\w{2})')
    sizes = [package["size"] for package in packages]
    size_matched = [item for item in [re.search(regex, size) for size in sizes] if item is not None]
    for size in size_matched:
        number = int(size.group(1))
        unit = size.group(2).strip()
        if unit in SIZE_COEFFICIENTS.keys():
            size_in_gb = number * SIZE_COEFFICIENTS[unit]
            total += size_in_gb
    return total


def compare_version(a, b):
    a_number = int(a[0])
    b_number = int(b[0])
    comparison = a_number - b_number
    if comparison is 0:
        return compare_version(a[1:], b[1:])
    return comparison


def determine_best_packages(packages):
    """
    Parses the list of packages provided and returns a list containing only unique packages in their highest version
    (removes outdated versions of each package).
    """
    package_dict = dict()
    for package in packages:
        package_tuple = separate_name_from_version(package["path"])
        name = package_tuple[0]
        version = package_tuple[1]
        if name not in package_dict.keys() or compare_version(package_dict[name], version) < 0:
            package_dict[name] = version

    best_package_paths = [key + '.'.join(value) + '.zip' for key, value in package_dict.items()]
    result = []
    for package in packages:
        if package["path"] in best_package_paths:
            result.append(package)

    return result


def separate_name_from_version(path):
    regex = re.compile(r'(^.*-)(\d{1,3}(\.\d{1,3})?(.\d{1,4})?)(\.zip)')
    parts = re.search(regex, path)
    return parts.group(1), parts.group(2).split('.')


def read_arguments():
    # Define args
    parser = argparse.ArgumentParser()
    parser.add_argument('-d', '--date', help='A date in the format YYYY-MM-DD')
    parser.add_argument('-f', '--force', help='Do not prompt user for confirmation before each package delete',
                        action='store_true')
    parser.add_argument('--host', help='The host URL of the AEM instance in the format host:port')
    parser.add_argument('-p', '--path',
                        help='A package sub-path (eg: "adobe" will search for packages under /etc/packages/adobe)')
    parser.add_argument('-u', '--user', help='User credentials in the format user:pass')
    parser.add_argument('-v', '--verbose', help='Logs more output', action='store_true')
    return parser.parse_args()


def set_argument(name, args):
    """
    Sets an argument to the value provided on the command line if present, otherwise to a default value. If no default
    exists, sets argument value to None.
    """
    arg = getattr(args, name)
    if "default" in ARGUMENT_DEFINITIONS[name].keys():
        default = ARGUMENT_DEFINITIONS[name]["default"]
        result = arg if arg is not None else default
    else:
        result = arg
    if args.verbose:
        print("{0} = {1}".format(name, result))
    return result


def validate_arguments(args):
    for key, value in ARGUMENT_DEFINITIONS.items():
        arg = getattr(args, key)
        if arg is not None:
            valid = re.match(value['test'], arg)
            if not valid:
                print("{0} argument value ({1}) is invalid".format(key, arg))


def confirm():
    """
    Asks user to confirm purge.
    """
    confirmation = input("Do you wish to continue? (y/n): ")
    delete = confirmation is 'y'
    if not delete:
        if confirmation is not 'n':
            print("Input not recognized. Aborting operation")
            exit(1)
        print("Aborting operation")
        exit(0)


def purge_packages(outdated_packages, host, credentials, verbose, force):
    """
    For each package provided, send a POST request to AEM to delete the package. AEM will delete both the package in
    question and all snapshots of the package (therefore no need to send requests to delete snapshots).
    """
    for package in outdated_packages:
        path = package["path"]
        print("Deleting " + path)
        confirmation = force or input("Do you wish to continue? (y/n)") is 'y'
        if confirmation:
            try:
                response = requests.post(
                    'http://{}/crx/packmgr/service/.json{}?cmd=delete'.format(
                        host, path),
                    auth=credentials)
                if response.status_code == 200:
                    print("Done" if response.json()["success"] else "Failed")
                    if verbose:
                        print(response.json())
                else:
                    print("Failed: {0}".format(re))
            except Exception as e:
                print("Could not reach AEM")
                if verbose:
                    print(e)


def print_size(size_to_remove, total_size):
    """
    Prints the size of the purge compared to the total size of all package data to the console.
    """
    if '.' in str(size_to_remove):
        after_decimal_point = str(size_to_remove).split('.')[1]
        number_of_leading_zeroes = len(after_decimal_point) - len(after_decimal_point.lstrip('0'))
        decimal_places = number_of_leading_zeroes + 2
    else:
        decimal_places = 0

    print("Purging outdated packages will remove {0} GB / {1} GB of package data".format(
        round(size_to_remove, decimal_places),
        round(total_size, decimal_places)))


def find_outdated_snapshots(packages, outdated_packages):
    """
    Finds all the snapshots that correspond to outdated packages provided.
    """
    outdated_package_paths = [package["path"] for package in outdated_packages]
    outdated_package_names = []

    for path in outdated_package_paths:
        name = get_package_name_from_path(path)
        if name is not None:
            outdated_package_names.append(name)

    result = []
    for package in packages:
        path = package["path"]
        if '.snapshot' in path and get_package_name_from_path(path) in outdated_package_names:
            result.append(package)
    return result


def get_package_name_from_path(path):
    regex = re.compile(r'(.*)(/)(.*\.zip)')
    search = re.search(regex, path)
    if len(search.groups()) is 3:
        return search.group(3)


main()
