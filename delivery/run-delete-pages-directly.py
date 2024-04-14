import logging
import os
import sys
import time
import shutil
from typing import Dict, List, Union
from git import Repo
import yaml
import json
import importlib.util

from ruamel.yaml import YAML

sys.path.append(f'{os.path.dirname(__file__)}/..')
import subprocess
import utils.myutils as mu
from utils.myutils import file_exists, mkdir_p
from utils.github_apis import GitHubAPIs
from os import listdir
from os.path import isfile, join

github_token = os.environ['GITHUB_APP_TOKEN']
organisation = 'glcp'
repositories = []
headers = {
    'Authorization': f'Bearer {github_token}',
    'Content-Type': 'application/json'
}

logger: Union[logging.Logger, None] = None
gh_obj = None
topdir = os.path.dirname(os.path.abspath(sys.argv[0]))
logdir = f'{topdir}/logdir'


def main():
    cwd = os.getcwd()
    yaml_file_path = f'{cwd}/workflow-deployment.yaml'
    repositories = get_repository_names_from_yaml(yaml_file_path)
    print(repositories)
    if not 'ORG_NAME' in os.environ:
        org_name = 'glcp'
    else:
        org_name = os.environ['ORG_NAME']
    global managed_ci_workflow_repo
    managed_ci_workflow_repo = 'managed-ci-workflow'

    app_token = os.environ.get("GITHUB_APP_TOKEN", '')

    mu.mkdir_p(logdir)
    global logger
    global gh_obj
    ## Change values accordingly in get_logger()
    logger = mu.get_logger('workflow-deployer', f'{logdir}/workflow-deployer.log', level='debug',
                           output_to_console=True)
    gh_obj = GitHubAPIs(org_name=org_name, token=app_token, logger=logger)
    org_repos: List[str] = gh_obj.get_repo_names_in_org()

    for repo in repositories:
        if repo not in org_repos:
            raise Exception(f"Repository {repo} not found in {org_name} organization")

        if gh_obj.check_is_repo_archived(repo):
            logger.info(f'Repo "{repo}" is Archived ...Skipping')
            continue

        # Clone participating project repo
        git_clone(org_name, repo, app_token)
        current_wd = os.getcwd()

        # Read data from mci-variable.yaml file
        gh_pages_retention_days = get_gh_pages_retention_days(repo,
                                                              file_path=f'{current_wd}/{repo}/.github/mci-variables.yaml',
                                                              key='RETENTION_DAYS')

        # Switch to gh-pages branch
        path = f'{cwd}/{repo}'

        branch_name = 'gh-pages'
        try:
            repo = Repo(path)
            repo.git.checkout(branch_name)
            print(f"Switched to branch '{branch_name}' successfully.")
        except Exception as e:
            logger.info(f"Error: Unable to switch to branch '{branch_name}'. {e}")
            continue

        # calculates the age of index.html file
        print(f'current working directory {path}')
        dir_to_delete = []
        for dir_name in os.listdir(path):
            dir_path = os.path.join(path, dir_name)
            print(dir_path)
            if os.path.isdir(dir_path):
                files = os.listdir(dir_path)
                if 'index.html' in files:
                    file_path = os.path.join(dir_path, 'index.html')
                    modification_time = os.path.getmtime(file_path)
                    current_time = time.time()
                    age_in_days = (current_time - modification_time) / (24 * 3600)
                    if age_in_days > gh_pages_retention_days:
                        logger.info(f"File 'index.html' in {dir_name} is {age_in_days} days old.")
                        dir_to_delete.append(dir_path)

        # deletes/ skip the folders/repo from deletion as per logic
        if len(dir_to_delete) > 0:
            for directory in dir_to_delete:
                delete_directory(directory)
            commit_and_push_changes(repo_path=path, commit_message="Auto deleting older files", branch=branch_name)

def get_repository_names_from_yaml(file_path):
    repository_names = []
    with open(file_path, 'r') as file:
        data = yaml.safe_load(file)
        # Check if 'modules' key exists and iterate through each module
        if 'modules' in data:
            for module in data['modules']:
                # Check if 'name' is 'managed-ci-workflow' and 'repositories' key exists
                if module.get('name') == 'managed-ci-workflow' and 'repositories' in module:
                    # Iterate through each repository and append its name to the list
                    for repository in module['repositories']:
                        repository_names.append(repository['name'])
    return repository_names


def git_clone(org_name: str, repo_name: str, token: str, refspec='gh-pages', directory=None):
    logger.debug(f"git clone {org_name}/{repo_name}")
    git_url = f'https://x-access-token:{token}@github.com/{org_name}/{repo_name}.git'
    cwd = os.getcwd()
    path = f'{cwd}/{repo_name}'
    try:
        Repo.clone_from(git_url, path)
    except:
        raise ValueError(f'{repo_name} does not exist')


def calculate_age_of_index(repo_name):
    """This function calculates the age of Index.html file"""
    # Define the root directory
    root_dir = repo_name

    # Iterate over the directories in the root directory
    for dir_name in os.listdir(root_dir):
        dir_path = os.path.join(root_dir, dir_name)

        # Check if it's a directory
        if os.path.isdir(dir_path):
            # Get the list of files in the directory
            files = os.listdir(dir_path)
            for file in files:
                # Check if 'index.html' exists in the directory
                if 'index.html' in file:
                    file_path = os.path.join(dir_path, 'index.html')
                    # Get the modification time of the file
                    modification_time = os.path.getmtime(file_path)
                    # Get the current time
                    current_time = time.time()
                    # Calculate the age of the file in days
                    age_in_days = (current_time - modification_time) / (24 * 3600)
                    print(f"File 'index.html' in {dir_name} is {age_in_days:.2f} days old.")


def get_gh_pages_retention_days(repo, file_path='.github/mci-variables.yaml', key='RETENTION_DAYS'):
    """This function fetches the retention days for files in gh-pages branch"""
    value = 0
    try:
        with open(file_path, 'r') as file:
            data = yaml.safe_load(file)
            value = data.get(key)
    except FileNotFoundError:
        logger.info(f'File {file_path} not found.')
    except yaml.YAMLError as e:
        logger.info(f"Error reading YAML file '{file_path}': {e}")
    if value != 0:
        logger.info(f"gh-pages retention days for '{repo}' is: {value}")
        return value
    else:
        value = 180
        return value


def delete_directory(directory_path):
    try:
        shutil.rmtree(directory_path)
        logger.info(f"Directory '{directory_path}' deleted successfully.")
    except FileNotFoundError:
        logger.info(f"Directory '{directory_path}' not found.")
    except PermissionError:
        logger.info(f"Permission denied to delete directory '{directory_path}'.")


def commit_and_push_changes(repo_path, commit_message, branch="gh-pages"):
    try:
        repo = Repo(repo_path)
        # dir_list = ["folder1", "folder2", "folder3", "folder4", "folder5"]
        # for item in os.listdir(repo_path):
        #     print(item)
        # for directory in dir_list:
        #     delete_dir = f'{repo_path}/{directory}'
        #     if os.path.exists(delete_dir) and os.path.isdir(delete_dir):
        #         try:
        #             # Delete the directory
        #             print('trying to delete {directory}')
        #             shutil.rmtree(delete_dir)
        #             print(f"Directory '{delete_dir}' deleted successfully.")
        #         except OSError as e:
        #             print(f"Error deleting directory '{delete_dir}': {e}")
        #     else:
        #         print(f"Directory '{delete_dir}' does not exist.")

        diff_output = subprocess.check_output(['git', 'diff'], cwd=repo_path)
        # print(diff_output)

        if diff_output:
            os.chdir(repo_path)
            repo.index.add("*")
            subprocess.run(['git', 'add', '.'])
            subprocess.run(['git', 'commit', '-m', commit_message])
            subprocess.run(['git', 'push', '--set-upstream', 'origin', branch])
            repo.index.commit(commit_message)
            origin = repo.remote()
            origin.push(refspec=f"refs/heads/{branch}")
            logger.info(f"Changes committed and pushed successfully.")
        else:
            logger.info(f"No changes to commit.")

    except Exception as e:
        logger.info(f"An error occurred: {e}")


if __name__ == '__main__':
    main()
    
