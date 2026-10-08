"""Fixed admission-only CLI profile; activation requires actual boundary proof."""
import hashlib
import json
import os
import tomllib
from pathlib import Path
from ..canonical import canonical_json, sha256_hex
from ..interpreter_provider import CodexProvider, DISABLED_FEATURES, MODEL

PROFILE = 'jasmine-p2-admission'


def user_surface():
    """Nonsecret configuration metadata only; never launch inherited services."""
    home = Path.home() / '.codex'
    config = tomllib.loads((home / 'config.toml').read_text()) if (home / 'config.toml').exists() else {}
    hooks_file = home / 'hooks.json'
    raw = hooks_file.read_bytes() if hooks_file.exists() else b''
    hooks = json.loads(raw).get('hooks', {}) if raw else {}
    inline = config.get('hooks', {})
    if any(entries for entries in hooks.values()) or any(value for key,value in inline.items() if key != 'state'):
        raise ValueError('non-fixture user hook definitions are not authorized; no global hooks changed')
    return {'mcp_server_names': sorted(config.get('mcp_servers', {})),
            'global_hook_sha256': hashlib.sha256(raw).hexdigest(),
            'legacy_sandbox_mode_present': 'sandbox_mode' in config}


def configuration(executable, catalog, fixture, private_root, code_root):
    provider = CodexProvider(str(executable), str(catalog))
    if not provider.valid:
        raise ValueError('unsupported fixed executable/catalog')
    fixture, private_root, code_root = [Path(p).resolve(strict=True) for p in (fixture, private_root, code_root)]
    roots = [fixture, private_root, code_root]
    for index, first in enumerate(roots):
        for second in roots[index+1:]:
            if first == second or first in second.parents or second in first.parents:
                raise ValueError('fixture/runtime/code roots must not overlap')
    # Root and every wrapper/config ancestor are read-only. Only work/ permits
    # writes, so rename/replacement of .codex or scripts via parent is excluded.
    filesystem = {':minimal': 'read', str(fixture): 'read', str(fixture / 'work'): 'write',
                  str(code_root): 'read', str(private_root): 'deny'}
    value = {'profile_version': 'admission-only.v2', 'normal_user_trust_loading': True, 'user_surface': user_surface(), 'model': MODEL,
        'provider': provider.configuration(), 'filesystem': filesystem, 'command_network': False,
        'approval_policy': 'never', 'mode': 'default', 'hooks_enabled': True,
        'disabled_features': [f for f in DISABLED_FEATURES if f != 'hooks'],
        'project_doc_max_bytes': 0, 'skills_include_instructions': False,
        'skills_bundled_enabled': False, 'tool_env_exclude': ['JASMINE_*'],
        'fixture': str(fixture), 'private_root': str(private_root), 'code_root': str(code_root)}
    value['config_digest'] = sha256_hex(canonical_json(value))
    return value


def argv(config, *, session=None, output=None):
    frozen = configuration(config['provider']['executable_path'], config['provider']['catalog_path'],
                           config['fixture'], config['private_root'], config['code_root'])
    if config != frozen:
        raise ValueError('main profile configuration changed')
    executable = config['provider']['executable_path']
    if hashlib.sha256(Path(executable).read_bytes()).hexdigest() != config['provider']['executable_sha256']:
        raise ValueError('executable changed')
    catalog = config['provider']['catalog_path']
    if hashlib.sha256(Path(catalog).read_bytes()).hexdigest() != config['provider']['catalog_sha256']:
        raise ValueError('catalog changed')
    result = [executable, 'exec']
    if session:
        result += ['resume', session]
    result += ['--skip-git-repo-check', '-m', MODEL]
    options = {'approval_policy': 'never', 'default_permissions': PROFILE,
        f'permissions.{PROFILE}.filesystem': config['filesystem'],
        f'permissions.{PROFILE}.network.enabled': False,
        'model_provider': 'interpreter-openai',
        'model_providers.interpreter-openai': {'name': 'OpenAI', 'wire_api': 'responses', 'requires_openai_auth': True, 'supports_websockets': False},
        'model_catalog_json': catalog, 'project_doc_max_bytes': 0, 'skills.include_instructions': False,
        'skills.bundled.enabled': False, 'web_search': 'disabled', 'agents.enabled': False,
        'features.hooks': True, 'features.default_mode_request_user_input': False,
        'memories.use_memories': False, 'memories.generate_memories': False,
        'shell_environment_policy.exclude': ['JASMINE_*'], 'notify': [], 'features.multi_agent_v2': False}
    options['mcp_servers'] = {name: {'command': '/usr/bin/false', 'args': [], 'enabled': False}
                              for name in config['user_surface']['mcp_server_names']}
    # TOML inline tables, not JSON objects, are required by CLI -c parsing.
    def toml(value):
        if isinstance(value, dict):
            return '{' + ','.join(json.dumps(k) + '=' + toml(v) for k,v in value.items()) + '}'
        return json.dumps(value, ensure_ascii=False)
    for feature in config['disabled_features']:
        options['features.' + feature] = False
    for name, value in options.items():
        result += ['-c', name + '=' + toml(value)]
    result += ['--json']
    if output:
        result += ['-o', str(output)]
    if not session:
        result += ['-C', config['fixture']]
    return result + ['-']


def environment(nonce):
    value = {key: os.environ[key] for key in ('HOME', 'PATH', 'TMPDIR', 'LANG', 'LC_ALL', 'SYSTEMROOT') if key in os.environ}
    value['CODEX_HOME'] = str(Path.home() / '.codex')
    value['JASMINE_CORE_GATE_NONCE'] = nonce
    return value


def trust_argv(config):
    """Normal interactive /hooks UI with the same frozen security overrides.

    No nonce, bypass, synthetic trust hash or session resume is supplied.
    """
    commands = argv(config)
    return [commands[0]] + [value for value in commands[2:] if value not in ('--skip-git-repo-check', '--json', '-')]
