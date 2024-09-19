# Upload twister results

Simple Python's script which uploads twister results from a given folder
to a forked repository and creates a pull request to upstream repository.

Usage:
```shell
export GITHUB_TOKEN="your Github token"
python results_uploader.py --token=$GITHUB_TOKEN --results-directory results --pattern "*.json" \
--title "My PR" --body "Some PR's description"
```

Example ``uploader.ini`` file:
```ini
[uploader]
forked_repo = git@github.com:username/repo-name.git
upstream_repo = https://github.com/upstream-name/repo-name.git
; optional
author_name = Firstname Lastname
author_email = firstname.lastname@mail.com
branch_name_pattern = results-%Y%m%d%H%M%S
commit_message = Test results
```
