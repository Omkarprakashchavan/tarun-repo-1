#!/usr/bin/env python3
import requests
import json
import sys
import yaml
import os

api_url = 'https://api.github.com/graphql'
github_token = os.environ['GITHUB_APP_TOKEN']
organisation = 'glcp'
repositories = []
repository_ids = []

def print_red(skk): print("\033[91m {}\033[00m" .format(skk))

def print_green(skk): print("\033[92m {}\033[00m" .format(skk))

# Access variables

# Read the YAML configuration file
with open("config.yaml", "r") as config_file:
    config = yaml.safe_load(config_file)
try:
    default_tag_status_context = config['default_tag_status_context']
except KeyError:
    print_red("default_tag_status_context is not available in deployer-config.yaml")
    default_tag_status_context = []
try:
    default_language_context = config['language']
except KeyError:
    print_red("language is not available in deployer-config.yaml")
    default_language_context = {}
try: 
    secrets = config["secrets"]
except KeyError:
    print_red("secrets is not available in deployer-config.yaml")
    secrets = []
try:
    lang_variable = config["lang_variable"]
except KeyError:
    print_red("lang_variable is not available in deployer-config.yaml")
    lang_variable = ''
try:
    required_status_check_contexts = config["required_status_check_contexts"]
except KeyError:
    print_red("required_status_check_contexts is not available in deployer-config.yaml")
    required_status_check_contexts = []

headers = {
    'Authorization': f'Bearer {github_token}',
    'Content-Type': 'application/json'
}

try:
    sys.argv[1]
except IndexError:
    sys.exit("workflow-deployment.yaml file expected as input argument")

def main():
    """This function process YAML input file and fetches repository names into list"""
    file_path = sys.argv[1]
    with open(file_path, 'r') as file:
        yaml_content = file.read()

    # Parse the YAML content
    parsed_yaml = yaml.safe_load(yaml_content)

    # Fetch repositories names into a list
    repositories = []
    modules = parsed_yaml.get('modules', [])
    for module in modules:
        repositories.extend([repo['name'] for repo in module.get('repositories', [])])
    globals()["repositories"] = repositories
    if bool(repositories):
        create_list_repo_ids(repositories)
    else:
        print_red("Unable to fetch repository names from input file")

def create_list_repo_ids(repositories):
    """This functions creates list of repository ids where secret access needs to be updated"""
    auth_header = {'Authorization': 'token '+github_token, 'X-GitHub-Api-Version': '2022-11-28', 'Accept': 'application/vnd.github+json'}
    repository_ids = []
    for repo in repositories:
        githubapi = f"https://api.github.com/repos/{organisation}/{repo}"
        repo_response = requests.get(githubapi, headers=auth_header)
        repository_ids.append(repo_response.json()['id'])
    globals()["repository_ids"] = repository_ids
    if bool(repository_ids):
        update_secret_access_to_repo(repository_ids)
    else:
        print_red("Unable to create list of repository ids")

def update_secret_access_to_repo(repository_ids):
    """This function adds the repositories for to access Organisation secrets"""
    headers = {
        'Accept': 'application/vnd.github+json',
        'Authorization': f'Bearer {github_token}',
        'X-GitHub-Api-Version': '2022-11-28'
    }
    if len(secrets) != 0:
        for secret in secrets:
            url = f"https://api.github.com/orgs/{organisation}/actions/secrets/{secret}/repositories"
            try:
                response = requests.get(url, headers=headers)
                response.raise_for_status()
                response_data = json.loads(response.text)
                existing_repo_ids = [rule ["id"] for rule in response_data["repositories"]]
                repo_ids = list(set(existing_repo_ids + repository_ids))
                data = {
                    "selected_repository_ids": repo_ids
                }
            except requests.exceptions.RequestException as e:
                print_red(f'Error while fetching existing repository names for {secret} secret')
            try:
                response = requests.put(url, headers=headers, json=data)
                response.raise_for_status()
                print_green(f'Updated access to the {secret} secret for all given repos')
            except requests.exceptions.RequestException as e:
                print_red(f"Failed to update the secret access for repository. Status code: {response.status_code} {str(e)}")
    else:
        print_red("Secrets are not defined in deployer-config.yaml file")

def check_repo_exist(repository, refspec, optional_workflows, language):
    """ This Function checks if given Github Repo exists """
    query = f'''
    query {{
        repository(owner: "{organisation}", name: "{repository}") {{
            id
        }}
    }}
    '''
    try:
        response = requests.post(api_url, json={"query": query}, headers=headers)
        response.raise_for_status()
        repository_id = response.json().get("data", {}).get("repository")['id']
        if repository_id:
            get_default_branch(repository_id, repository, refspec, optional_workflows, language)
        else:
            print_red(f"The repository '{repository}' does not exist.")
            raise ValueError("The repository '{repository}' does not exist.")
    except requests.exceptions.RequestException as e:
        print_red(f"Failed to query GitHub GraphQL API. Status code: {response.status_code} {str(e)}")
        raise ValueError("The repository '{repository}' does not exist.")

def get_default_branch(repository_id, repository, refspec, optional_workflows, language):
    """ This Function gets default branch for given repo """
    query = f'''
    query {{
    repository(owner: "{organisation}", name: "{repository}") {{
        defaultBranchRef {{
        name
        }}
    }}
    }}
    '''
    variables = {
        "owner": organisation,
        "name": repository
    }
    
    try:
        response = requests.post(api_url, headers=headers, json={'query': query, 'variables': variables})
        response.raise_for_status()
        default_branch = response.json()['data']['repository']['defaultBranchRef']['name']
        check_if_branch_protected(repository, repository_id, default_branch, refspec, optional_workflows, language)
    except requests.exceptions.RequestException as e:
        print_red(f"Failed to retrieve the default branch. Status code:, {str(e)}")

def check_if_branch_protected(repository, repository_id, default_branch, refspec, optional_workflows, language):
    """ This function checks if branch protection is already enabled for default branch"""
    query = '''
    query {
    repository(owner: "%s", name: "%s") {
        branchProtectionRules(first: 100) {
        nodes {
            id
            requiredStatusCheckContexts
            pattern
        }
        }
    }
    }
    ''' % (organisation, repository)
    try:
        response = requests.post(api_url, json={"query": query}, headers=headers)
        response.raise_for_status()
        data = response.json().get("data", {}).get("repository", {}).get("branchProtectionRules", {}).get("nodes", [])
        response_data = json.loads(response.text)
        protected_branches = [rule["pattern"] for rule in response_data["data"]["repository"]["branchProtectionRules"]["nodes"]]
        if default_branch not in protected_branches:
            create_branchprotection_rule(repository, repository_id, default_branch, refspec, optional_workflows, language)
        else:
            for rule in response_data["data"]["repository"]["branchProtectionRules"]["nodes"]:
                if rule["pattern"] == default_branch:
                    protection_rule_id = rule["id"]
                    protected_status_check_context = rule["requiredStatusCheckContexts"]
                    updated_status_check_context = evaluate_context_for_bpr(refspec, repository, protected_status_check_context)
                    update_branchprotection_rule(repository, protection_rule_id, default_branch, updated_status_check_context)       
    except requests.exceptions.RequestException as e:
        print_red(f"Failed to query GitHub GraphQL API. Status code: {response.text} {str(e)}")
        create_branchprotection_rule(repository, repository_id, default_branch, refspec, optional_workflows, language)

def create_branchprotection_rule(repository, repository_id, default_branch, refspec, optional_workflows, language):
    """ This function creates the branch protection rule """
    protected_status_check_context = []
    updated_status_check_context = evaluate_context_for_bpr(refspec, repository, protected_status_check_context)
    query = f'''
    mutation {{
        createBranchProtectionRule(input: {{
        clientMutationId: "uniqueId",
        repositoryId: "{repository_id}",
        pattern: "{default_branch}",
        requiredStatusCheckContexts: {updated_status_check_context},
        requiresStatusChecks: true,
        requiresStrictStatusChecks: true
    }}) {{
        branchProtectionRule {{
        id
        pattern
        requiredStatusCheckContexts
        requiresStatusChecks
        requiresStrictStatusChecks
        }}
    }}
    }}
    '''
    try:
        response = requests.post(api_url, json={"query": query}, headers=headers)
        response.raise_for_status()
        try:
            data = response.json().get("data", {}).get("createBranchProtectionRule", {}).get("branchProtectionRule", {})
        except AttributeError:
            print(f'Failed to create branch protection rule for {repository}. Response: {response.text}')
        if data:
            print_green(f'Branch protection rule created successfully for {repository} on default branch {default_branch}.')
            print("Required Status Check Contexts:", data["requiredStatusCheckContexts"])

        else:
            print_red(f'Failed to create branch protection rule for {repository} on default branch {default_branch}.')
    except requests.exceptions.RequestException as e:
        print_red(f"Failed to query GitHub GraphQL API. Response: {response.text} {str(e)}")

def update_branchprotection_rule(repository, protection_rule_id, default_branch, updated_status_check_context):
    """ This function updates the branch protection rule """
    query = f'''
    mutation {{
    updateBranchProtectionRule(input: {{
        clientMutationId: "uniqueId1",
        branchProtectionRuleId: "{protection_rule_id}"
        pattern: "{default_branch}",
        requiredStatusCheckContexts: {updated_status_check_context},
        requiresStatusChecks: true,
        requiresStrictStatusChecks: true
    }}) {{
        branchProtectionRule {{
        id
        pattern
        requiredStatusCheckContexts
        requiresStatusChecks
        requiresStrictStatusChecks
        }}
    }}
    }}
    '''
    try:
        response = requests.post(api_url, json={"query": query}, headers=headers)
        response.raise_for_status()
        try:
            data = response.json().get("data", {}).get("updateBranchProtectionRule", {}).get("branchProtectionRule", {})
        except AttributeError:
            print_red(f'Failed to update branch protection rule for {repository}. Response: {response.text}')
        if data:
            print_green(f'Branch protection rule updated successfully for {repository} on default branch "{default_branch}".')
            print(f"Required Status Check Contexts for {repository} on branch {default_branch} updated as: ", data["requiredStatusCheckContexts"])
        else:
            print_red(f'Failed to updated branch protection rule for {repository} on default branch "{default_branch}".')
    except requests.exceptions.RequestException as e:
        print_red(f"Failed to query GitHub GraphQL API. Response: {response.text} {str(e)}")

def evaluate_context_for_bpr(refspec, repository, protected_status_check_context):
    """This function evaluates the CONTEXT for branch protection rule and returns the context and language"""
    global default_tag_status_context
    tag_status_context = []
    if refspec.startswith("tags/v1.1"):
        try:
            tag_status_context = default_tag_status_context['tags_1x_status_context']
        except KeyError:
            print_red("Tag status context not available for tags_1x_status_context in deployer-config.yaml")
    elif refspec.startswith("tags/v1.2"):
        try:
            tag_status_context = default_tag_status_context['tags_2x_status_context']
        except KeyError:
            print_red("Tag status context not available for tags_2x_status_context in deployer-config.yaml")
    elif refspec.startswith("tags/v1.3"):
        try:
            tag_status_context = default_tag_status_context['tags_3x_status_context']
        except KeyError:
            print_red("Tag status context not available for tags_3x_status_context in deployer-config.yaml")
    else:
        tag_status_context = []
    #print(f"tag_status_context {tag_status_context}")

    # Get lanaguage variable value for this repository
    try:
        response = requests.get(url=f"https://api.github.com/repos/{organisation}/{repository}/actions/variables/{lang_variable}", headers=headers)
        response.raise_for_status()
        language = response.json()['value']
        global default_language_context
        if language in default_language_context:
            language_context = default_language_context[language]
        else:
            print_red(f"{lang_variable} status check context not found in deployer-config.yaml")
            language_context = []
    except requests.exceptions.RequestException:
        print_red(f'{lang_variable} repository variable not found in {repository}.')
        language_context = []
    global required_status_check_contexts
    join_status_context = list(set(protected_status_check_context + required_status_check_contexts + tag_status_context + language_context))
    temp_list = ', '.join(f'"{item}"' for item in join_status_context)
    updated_status_check_context = '['+temp_list+']'
    return updated_status_check_context

main()

file_path = sys.argv[1]
with open(file_path, 'r') as file:
    yaml_content = file.read()

# Parse the YAML content
parsed_yaml = yaml.safe_load(yaml_content)
for module in parsed_yaml.get('modules', []):
    repositories = module.get('repositories', [])
    for repository in repositories:
        repo_name = repository.get('name')
        refspec = repository.get('refspec')
        try:
            optional_workflows = repository.get('optional_workflows', [])
        except IndentationError:
            optional_workflows = []
        try:
            language = repository.get('language', [])
            language = ', '.join(language)
        except IndentationError:
            language = ''
        try:
            check_repo_exist(repo_name, refspec, optional_workflows, language)
        except Exception as e:
            print_red(f'Error while working on {repo_name}: {str(e)}')
            continue
