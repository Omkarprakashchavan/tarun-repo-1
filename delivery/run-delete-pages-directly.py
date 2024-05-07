import logging
import os
import sys
import time
import shutil
import subprocess
from typing import Dict, List, Union
from datetime import datetime
from git import Repo

import yaml
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

def main(module_name='', module_description='', repositories=[], default_managed_refspec=None):
    filename='workflow-deployment.yaml'
    with open(filename, "r") as yaml_file:
        data = yaml.safe_load(yaml_file)
    modules=data['modules']
    print(f'workflow deployment config:\n{yaml.dump(modules, default_flow_style=False)}')

    for i in modules:
        module_name=i.get('name')
        module_description=i.get('description')
        repositories=i.get('repositories', [])
        print(repositories)

    if not 'ORG_NAME' in os.environ:
        org_name='glcp'
    else:
        org_name=os.environ['ORG_NAME']
    global managed_ci_workflow_repo
    managed_ci_workflow_repo='managed-ci-workflow'

    app_token = os.environ.get("GITHUB_APP_TOKEN", '')

    mu.mkdir_p(logdir)
    global logger
    global gh_obj
    ## Change values accordingly in get_logger()
    logger = mu.get_logger('workflow-deployer', f'{logdir}/workflow-deployer.log', level='debug', output_to_console=True)
    gh_obj = GitHubAPIs(org_name=org_name, token=app_token, logger=logger)
    org_repos : List[str] = gh_obj.get_repo_names_in_org()

    logger.debug(f'Final list of Repos in the glcp org')

    for repo in repositories:
        r = repo.get('name')
        if r not in org_repos:
            raise Exception(f"Repository {r} not found in {org_name} organization")

        if gh_obj.check_is_repo_archived(r):
            logger.info(f'Repo "{r}" is Archived ...Skipping')
            continue

        # Clone participating project repo
        git_clone(org_name, r, app_token)
        current_wd = os.getcwd()

        # Read data from mci-variable.yaml file
        gh_pages_retention_days = get_gh_pages_retention_days(r, file_path=f'{current_wd}/{r}/.config', key='RETENTION_DAYS')
        if gh_pages_retention_days == 0:
            logger.info(f'RETENTION_DAYS set to 0, hence skipping Repo "{r}"')
            continue

        # Switch to gh-pages branch
        path = f'{current_wd}/{r}'
        
        branch_name = 'gh-pages'
        try:
            repo = Repo(path)
            repo.git.checkout(branch_name)
            logger.info(f"Switched to branch '{branch_name}' successfully.")
        except Exception as e:
            logger.info(f"Error: Unable to switch to branch '{branch_name}'. {e}")
            continue

        # calculates the age of index.html file
        print(f'current working directory {path}')
        dir_to_delete = []
        for dir_name in os.listdir(path):
            dir_path = os.path.join(path, dir_name)
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
                logger.info(f"Will delete {dir_to_delete}")
        #         delete_directory(directory)
        # commit_and_push_changes(repo_path=path, commit_message="Auto deleting older files", branch=branch_name) 

def git_clone(org_name: str, repo_name: str, token: str, refspec='gh-pages', directory=None):
    logger.debug(f"git clone {org_name}/{repo_name}")
    git_url=f'https://x-access-token:{token}@github.com/{org_name}/{repo_name}.git'
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
            
            # Check if 'index.html' exists in the directory
            if 'index.html' in files:
                file_path = os.path.join(dir_path, 'index.html')
                # Get the modification time of the file
                modification_time = os.path.getmtime(file_path)
                # Get the current time
                current_time = time.time()
                # Calculate the age of the file in days
                age_in_days = (current_time - modification_time) / (24 * 3600)
                print(f"File 'index.html' in {dir_name} is {age_in_days:.2f} days old.")

def get_gh_pages_retention_days(r, file_path='.github/mci-variables.yaml', key='RETENTION_DAYS'):
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
        logger.info(f"gh-pages retention days for '{r}' is: {value}")
        return value
    else:
        value = 180
        return value

# def delete_directory(directory_path):
#     try:
#         shutil.rmtree(directory_path)
#         logger.info(f"Directory '{directory_path}' deleted successfully.")
#     except FileNotFoundError:
#         logger.info(f"Directory '{directory_path}' not found.")
#     except PermissionError:
#         logger.info(f"Permission denied to delete directory '{directory_path}'.")

def commit_and_push_changes(repo_path, commit_message, branch="gh-pages"):
    try:
        repo = Repo(repo_path)
        dir_list = ["folder1", "folder2", "folder3", "folder4", "folder5"]
        for item in os.listdir(repo_path):
            print(item)
        for directory in dir_list:
            delete_dir = f'{repo_path}/{directory}'
            if os.path.exists(delete_dir) and os.path.isdir(delete_dir):
                try:
                    # Delete the directory
                    print('trying to delete {directory}')
                    shutil.rmtree(delete_dir)
                    print(f"Directory '{delete_dir}' deleted successfully.")
                except OSError as e:
                    print(f"Error deleting directory '{delete_dir}': {e}")
            else:
                print(f"Directory '{delete_dir}' does not exist.")

        diff_output = subprocess.check_output(['git', 'diff'], cwd=repo_path)
        print(diff_output)
        diff_status = subprocess.check_output(['git', 'status'], cwd=repo_path)
        for item in os.listdir(repo_path):
            print(item)
        print(diff_status)
        if diff_output:
            repo.index.add("*")
            repo.index.commit(commit_message)
            origin = repo.remote()
            origin.push(refspec=f"refs/heads/{branch}")
            logger.info(f"Changes committed and pushed successfully.")
        else:
            logger.info(f"No changes to commit.")

    except Exception as e:
        logger.info(f"An error occurred: {e}")

main()
