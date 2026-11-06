#!/usr/bin/env python3
"""CodeBuild-only publication; requires explicit execution approval."""
import json,os,shutil,subprocess,tempfile
REGION='ap-northeast-2'
ACCOUNT='525091405886'
REPOSITORY='hopcharlie/apps/hyperdx-prometheus-auth'
def aws(*args,allow_not_found=False):
    p=subprocess.run(['aws','--region',REGION,*args,'--output','json'],capture_output=True,text=True,check=False)
    if p.returncode:
        if allow_not_found and ('RepositoryNotFoundException' in p.stderr or 'ImageNotFoundException' in p.stderr):return None
        raise RuntimeError('Registry request failed; response suppressed')
    return json.loads(p.stdout)
def publish(source_sha):
    import re
    if not re.fullmatch('[0-9a-f]{40}',source_sha):raise ValueError('Expected full immutable source SHA')
    if not os.environ.get('CODEBUILD_BUILD_ID','').startswith('hopcharlie-app-build:'):raise RuntimeError('Publication restricted to existing CodeBuild project')
    if aws('sts','get-caller-identity')['Account']!=ACCOUNT:raise RuntimeError('Wrong AWS account')
    repository=aws('ecr','describe-repositories','--repository-names',REPOSITORY,allow_not_found=True)
    if repository is None:
        repository={'repositories':[aws('ecr','create-repository','--repository-name',REPOSITORY,'--image-tag-mutability','IMMUTABLE')['repository']]}
    repo=repository['repositories'][0]
    if repo['repositoryUri']!=f'{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com/{REPOSITORY}' or repo['imageTagMutability']!='IMMUTABLE':raise RuntimeError('Unexpected registry configuration')
    tag='2.39.1-prom-auth-'+source_sha[:12]
    if aws('ecr','describe-images','--repository-name',REPOSITORY,'--image-ids','imageTag='+tag,allow_not_found=True):raise RuntimeError('Immutable image tag already exists; refusing overwrite')
    registry=f'{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com';uri=repo['repositoryUri']+':'+tag
    docker_config=tempfile.mkdtemp(prefix='hyperdx-registry-');env={**os.environ,'DOCKER_CONFIG':docker_config}
    try:
        # This token never appears in argv, stdout, buildspec or repository files.
        p=subprocess.run(['aws','--region',REGION,'ecr','get-login-password'],capture_output=True,check=True)
        subprocess.run(['docker','login','--username','AWS','--password-stdin',registry],input=p.stdout,capture_output=True,check=True,env=env);del p
        subprocess.run(['docker','tag','hyperdx-prometheus-auth:verified',uri],check=True,env=env)
        subprocess.run(['docker','push',uri],check=True,env=env)
    finally:shutil.rmtree(docker_config)
    image=aws('ecr','describe-images','--repository-name',REPOSITORY,'--image-ids','imageTag='+tag)['imageDetails'][0]
    print(json.dumps({'sourceSha':source_sha,'image':repo['repositoryUri']+'@'+image['imageDigest'],'tag':tag}))
if __name__=='__main__':
    import sys
    publish(sys.argv[1])
