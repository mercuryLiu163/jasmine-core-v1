"""Read-only native app-server profile; native boundary proof remains required."""
from copy import deepcopy
from ..canonical import sha256_hex
from ..capture import p2_profile

PROFILE = 'jasmine-p3-native'

def configuration(executable, catalog, fixture, private_root, code_root):
    base = p2_profile.configuration(executable, catalog, fixture, private_root, code_root)
    value = deepcopy(base)
    value['profile_version'] = 'native-dynamic-readonly.v1'
    value['filesystem'][str(__import__('pathlib').Path(fixture).resolve() / 'work')] = 'read'
    value['skills_include_instructions'] = True
    value['reasoning_effort'] = 'low'
    value['native_dynamic_tools'] = ['jasmine_read', 'jasmine_patch', 'jasmine_test', 'jasmine_playwright']
    value.pop('config_digest')
    value['config_digest'] = sha256_hex(value)
    return value

def argv(config, *, interactive=False):
    original = p2_profile.configuration(config['provider']['executable_path'], config['provider']['catalog_path'],
        config['fixture'], config['private_root'], config['code_root'])
    frozen = configuration(config['provider']['executable_path'], config['provider']['catalog_path'],
        config['fixture'], config['private_root'], config['code_root'])
    if config != frozen:
        raise ValueError('native profile changed')
    old = p2_profile.argv(original)
    options = []
    for index, value in enumerate(old):
        if value == '-c':
            setting = old[index + 1]
            if setting.startswith('default_permissions='):
                setting = 'default_permissions="' + PROFILE + '"'
            elif setting.startswith('permissions.' + p2_profile.PROFILE + '.'):
                setting = setting.replace('permissions.' + p2_profile.PROFILE + '.', 'permissions.' + PROFILE + '.', 1)
                if '.filesystem=' in setting:
                    setting = setting.replace('"' + str(__import__('pathlib').Path(config['fixture']) / 'work') + '"="write"',
                                              '"' + str(__import__('pathlib').Path(config['fixture']) / 'work') + '"="read"')
            elif setting.startswith('skills.include_instructions='):
                setting = 'skills.include_instructions=true'
            options.extend(['-c', setting])
    options.extend(['-c','model_reasoning_effort="low"'])
    if interactive:
        return [old[0], '-m', config['model'], '-C', config['fixture']] + options
    return [old[0], 'app-server'] + options
