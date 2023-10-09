#!/usr/bin/env python3
import requests
import json
import sys
import yaml
import os

api_url = 'https://api.github.com/graphql'
github_token = os.environ['GITHUB_TOKEN']
organisation = 'glcp'
repositories = []
#print(repositories)
headers = {
    'Authorization': f'Bearer {github_token}',
    'Content-Type': 'application/json'
}
required_status_check_contexts = ["CONTEXT1", "CONTEXT3", "update-project-version"]

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

def check_repo_exist(repository):
    """ This Function checks if given Github Repo exists """
    query = f'''
    query {{
        repository(owner: "{organisation}", name: "{repository}") {{
            id
        }}
    }}
    '''
    response = requests.post(api_url, json={"query": query}, headers=headers)
    if response.status_code == 200:
        repository_id = response.json().get("data", {}).get("repository")['id']
        if repository_id:
            get_default_branch(repository_id, repository)
        else:
            print(f"The repository '{repository}' does not exist.")
            raise ValueError("The repository '{repository}' does not exist.")
    else:
        print(f"Failed to query GitHub GraphQL API. Status code: {response.status_code}")
        raise ValueError("The repository '{repository}' does not exist.")

def get_default_branch(repository_id, repository):
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
    response = requests.post(api_url, headers=headers, json={'query': query, 'variables': variables})

    if response.status_code == 200:
        default_branch = response.json()['data']['repository']['defaultBranchRef']['name']
        check_if_branch_protected(repository, repository_id, default_branch)
    else:
        print('Failed to retrieve the default branch. Status code:', response.status_code)
        raise ValueError("Failed to retrive default branch for repository {repository}.")

def check_if_branch_protected(repository, repository_id, default_branch):
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
    response = requests.post(api_url, json={"query": query}, headers=headers)

    if response.status_code == 200:
        data = response.json().get("data", {}).get("repository", {}).get("branchProtectionRules", {}).get("nodes", [])
        response_data = json.loads(response.text)
        protected_branches = [rule["pattern"] for rule in response_data["data"]["repository"]["branchProtectionRules"]["nodes"]]
        if default_branch not in protected_branches:
            create_branchprotection_rule(repository, repository_id, default_branch)
        else:
            for rule in response_data["data"]["repository"]["branchProtectionRules"]["nodes"]:
                if rule["pattern"] == default_branch:
                    protection_rule_id = rule["id"]
                    protected_status_check_context = rule["requiredStatusCheckContexts"]
                    updated_status_check_context = list(set(protected_status_check_context + required_status_check_contexts))
                    temp_list = ', '.join(f'"{item}"' for item in updated_status_check_context)
                    updated_status_check_context = '['+temp_list+']'
                    update_branchprotection_rule(repository, protection_rule_id, default_branch, updated_status_check_context)       
    else:
        print(f"Failed to query GitHub GraphQL API. Status code: {response.text}")
        raise ValueError("Failed to check branch protection rule for {repository}")

def create_branchprotection_rule(repository, repository_id, default_branch):
    """ This function creates the branch protection rule """
    global required_status_check_contexts
    temp_list = ', '.join(f'"{item}"' for item in required_status_check_contexts)
    status_check_contexts = '['+temp_list+']'
    query = f'''
    mutation {{
    createBranchProtectionRule(input: {{
        clientMutationId: "uniqueId",
        repositoryId: "{repository_id}",
        pattern: "{default_branch}",
        requiredStatusCheckContexts: {status_check_contexts},
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
    response = requests.post(api_url, json={"query": query}, headers=headers)

    if response.status_code == 200:
        try:
            data = response.json().get("data", {}).get("createBranchProtectionRule", {}).get("branchProtectionRule", {})
        except AttributeError:
            print(f'Failed to create branch protection rule for {repository}. Response: {response.text}')
        if data:
            print(f'Branch protection rule created successfully for {repository} on default branch {default_branch}.')
            print("Required Status Check Contexts:", data["requiredStatusCheckContexts"])

        else:
            print(f'Failed to create branch protection rule for {repository} on default branch {default_branch}.')
    else:
        print(f"Failed to query GitHub GraphQL API. Response: {response.text}")

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
    response = requests.post(api_url, json={"query": query}, headers=headers)

    if response.status_code == 200:
        try:
            data = response.json().get("data", {}).get("updateBranchProtectionRule", {}).get("branchProtectionRule", {})
        except AttributeError:
            print(f'Failed to update branch protection rule for {repository}. Response: {response.text}')
        if data:
            print(f'Branch protection rule updated successfully for {repository} on default branch "{default_branch}".')
            print("Required Status Check Contexts updated as: ", data["requiredStatusCheckContexts"])
        else:
            print(f'Failed to updated branch protection rule for {repository} on default branch "{default_branch}".')
    else:
        print(f"Failed to query GitHub GraphQL API. Response: {response.text}")

main()
for repo in repositories:
    try:
        check_repo_exist(repo)
    except Exception as e:
        print(f'Error while working on {repo}: {str(e)}')
        continue
