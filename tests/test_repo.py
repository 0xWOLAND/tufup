from datetime import datetime, timedelta
import json
import pathlib
from unittest.mock import patch, Mock

from securesystemslib.interface import generate_and_write_unencrypted_ed25519_keypair
from tuf.api.metadata import Metadata, Role, Root, TargetFile, TOP_LEVEL_ROLE_NAMES

import notsotuf.repo  # for patching
from notsotuf.repo import (
    Base, Keys, Roles, _in, SUFFIX_PUB, ROOT, TARGETS, SNAPSHOT, TIMESTAMP
)
from tests import TempDirTestCase


mock_input = Mock(return_value='')

DUMMY_SSL_KEY = {
    'keytype': 'ed25519',
    'scheme': 'ed25519',
    'keyid': '22f7c6046e29cfb0205a1c07941a5a57da39a6859b844f8c347f622a57ff82c8',
    'keyid_hash_algorithms': ['sha256', 'sha512'],
    'keyval': {'public': '93032b5804ba40a725145171193782bdfa30038584715546aea3228ea8018e46'},
}

DUMMY_ROOT = Root(
    version=1,
    spec_version='1.0',
    expires=datetime.now() + timedelta(days=1),
    keys=dict(),
    roles={role_name: Role(keyids=[], threshold=1) for role_name in TOP_LEVEL_ROLE_NAMES},
    consistent_snapshot=False,
)


class CommonTests(TempDirTestCase):
    def test_init(self):
        with patch('builtins.input', mock_input):
            # dir exists
            common = Base(dir_path=self.temp_dir_path, encrypted=[])
            self.assertTrue(common.dir_path.exists())
            # dir does not exist yet
            common = Base(dir_path=self.temp_dir_path / 'new', encrypted=[])
            self.assertTrue(common.dir_path.exists())
            self.assertFalse(common.encrypted)


class KeysTests(TempDirTestCase):
    def test_init_no_key_files(self):
        # no public key files exist yet
        keys = Keys(dir_path=self.temp_dir_path)
        for role_name in TOP_LEVEL_ROLE_NAMES:
            self.assertIsNone(getattr(keys, role_name))

    def test_init_import_public(self):
        # create some key files
        for role_name in TOP_LEVEL_ROLE_NAMES:
            filename = Keys.filename_pattern.format(role_name=role_name) + SUFFIX_PUB
            file_path = self.temp_dir_path / filename
            generate_and_write_unencrypted_ed25519_keypair(filepath=str(file_path))
        # test
        keys = Keys(dir_path=self.temp_dir_path)
        for role_name in TOP_LEVEL_ROLE_NAMES:
            self.assertIsInstance(getattr(keys, role_name), dict)

    def test_create(self):
        with patch('getpass.getpass', mock_input):
            keys = Keys(dir_path=self.temp_dir_path)
            keys.create()
            # key pair files should now exist
            filenames = [item.name for item in keys.dir_path.iterdir()]
            for role_name in TOP_LEVEL_ROLE_NAMES:
                private_key_filename = Keys.filename_pattern.format(role_name=role_name)
                public_key_filename = private_key_filename + SUFFIX_PUB
                self.assertIn(private_key_filename, filenames)
                self.assertIn(public_key_filename, filenames)
            # and the public keys should have been imported
            self.assertTrue(all(getattr(keys, n) for n in TOP_LEVEL_ROLE_NAMES))

    def test_public(self):
        keys = Keys(dir_path=self.temp_dir_path)
        # test empty
        self.assertFalse(keys.public())
        # set a dummy key value
        keys.root = DUMMY_SSL_KEY
        # test
        self.assertIn(DUMMY_SSL_KEY['keyid'], keys.public().keys())

    def test_roles(self):
        keys = Keys(dir_path=self.temp_dir_path)
        # test empty
        self.assertFalse(keys.roles())
        # set a dummy key value
        keys.root = DUMMY_SSL_KEY
        # test
        self.assertIn('root', keys.roles().keys())

    def test_find_private(self):
        # create dummy private key files in separate folders
        key_names = [('online', [SNAPSHOT, TIMESTAMP]), ('offline', [ROOT, TARGETS])]
        key_dirs = []
        for dir_name, role_names  in key_names:
            dir_path = self.temp_dir_path / dir_name
            dir_path.mkdir()
            key_dirs.append(dir_path)
            for role_name in role_names:
                filename = Keys.filename_pattern.format(role_name=role_name)
                (dir_path / filename).touch()
        # test
        for role_name in TOP_LEVEL_ROLE_NAMES:
            key_path = Keys.find_private(role_name=role_name, key_dirs=key_dirs)
            self.assertIn(role_name, str(key_path))
            self.assertTrue(key_path.exists())


class RolesTests(TempDirTestCase):
    def test_init(self):
        self.assertTrue(Roles(dir_path=self.temp_dir_path))

    def test_init_import_roles(self):
        def mock_from_file(filename, *args, **kwargs):
            return pathlib.Path(filename).exists()

        # create dummy metadata files
        for role_name in TOP_LEVEL_ROLE_NAMES:
            (self.temp_dir_path / f'{role_name}.json').touch()
        # test
        with patch.object(notsotuf.repo.Metadata, 'from_file', mock_from_file):
            roles = Roles(dir_path=self.temp_dir_path)
            self.assertTrue(all(getattr(roles, n) for n in TOP_LEVEL_ROLE_NAMES))

    def test_initialize(self):
        # prepare
        mock_keys = Mock()
        mock_keys.public = Mock()
        mock_keys.roles = Mock(return_value={n: None for n in TOP_LEVEL_ROLE_NAMES})
        roles = Roles(dir_path=self.temp_dir_path)
        # test
        roles.initialize(keys=mock_keys, expires=dict(root=_in(1)))
        self.assertTrue(
            all(isinstance(getattr(roles, n), Metadata) for n in TOP_LEVEL_ROLE_NAMES)
        )
        # files do not exist yet, because the roles still need to be populated
        self.assertFalse(any(roles.dir_path.iterdir()))

    def test_add_or_update_target(self):
        # prepare
        roles = Roles(dir_path=self.temp_dir_path)
        roles.targets = Mock(signed=Mock(targets=dict()))
        # test
        filename = 'my_app.gz'
        local_target_path = self.temp_dir_path / filename
        # path must exist
        with self.assertRaises(FileNotFoundError):
            roles.add_or_update_target(local_path=local_target_path)
        local_target_path.write_bytes(b'some bytes')
        # test
        for segments, expected_url_path in [
            (None, filename), ([], filename), (['a', 'b'], 'a/b/' + filename)
        ]:
            roles.add_or_update_target(
                local_path=local_target_path, url_path_segments=segments
            )
            with self.subTest(msg=segments):
                self.assertIsInstance(
                    roles.targets.signed.targets[expected_url_path], TargetFile
                )

    def test_add_public_key(self):
        # prepare
        roles = Roles(dir_path=self.temp_dir_path)
        roles.root = Mock(signed=Mock(roles=dict(), add_key=Mock()))
        public_key_path = self.temp_dir_path / 'targets_key.pub'
        public_key_path.write_text(json.dumps(DUMMY_SSL_KEY))
        # test
        role_name = 'targets'
        roles.add_public_key(role_name=role_name, public_key_path=public_key_path)
        self.assertTrue(roles.root.signed.add_key.called)

    def test_set_signature_threshold(self):
        # prepare
        role_name = 'targets'
        threshold = 2
        roles = Roles(dir_path=self.temp_dir_path)
        roles.root = Mock(signed=Mock(roles={role_name: Mock(threshold=1)}))
        # test
        roles.set_signature_threshold(role_name=role_name, threshold=threshold)
        self.assertEqual(threshold, roles.root.signed.roles[role_name].threshold)

    def test_set_expires(self):
        # prepare
        role_name = 'targets'
        expires = datetime.now() + timedelta(days=1)
        roles = Roles(dir_path=self.temp_dir_path)
        setattr(roles, role_name, Mock(signed=Mock(expires=None)))
        # test
        roles.set_expires(role_name=role_name, expires=expires)
        self.assertEqual(expires, getattr(roles, role_name).signed.expires)

    def test_sign_role(self):
        # prepare
        role_name = 'root'
        private_key_path = self.temp_dir_path / 'root_key'
        generate_and_write_unencrypted_ed25519_keypair(filepath=str(private_key_path))
        roles = Roles(dir_path=self.temp_dir_path)
        roles.root = Metadata(signed=DUMMY_ROOT, signatures=dict())
        # test
        roles.sign_role(
            role_name=role_name, private_key_path=private_key_path, encrypted=False
        )
        self.assertTrue(roles.root.signatures)

    def test_persist_role(self):
        # prepare
        roles = Roles(dir_path=self.temp_dir_path)
        roles.root = Metadata(signed=DUMMY_ROOT, signatures=dict())
        # test
        roles.persist_role(role_name='root')
        self.assertTrue((self.temp_dir_path / 'root.json').exists())

    def test_publish_root(self):
        with patch.object(Roles, '_publish_metadata', Mock()):
            # prepare
            roles = Roles(dir_path=self.temp_dir_path)
            roles.root = Mock(signed=Mock(version=1))
            roles.encrypted = []
            # test
            roles.publish_root(keys_dirs=[])
            self.assertEqual(2, roles.root.signed.version)
            self.assertTrue(Roles._publish_metadata.called)  # noqa

    def test_publish_targets(self):
        with patch.object(Roles, '_publish_metadata', Mock()):
            # prepare
            roles = Roles(dir_path=self.temp_dir_path)
            roles.targets = Mock(signed=Mock(version=1))
            roles.snapshot = Mock(
                signed=Mock(meta={'targets.json': Mock(version=1)}, version=1)
            )
            roles.timestamp = Mock(
                signed=Mock(snapshot_meta=Mock(version=1), version=1)
            )
            roles.encrypted = []
            # test
            roles.publish_targets(keys_dirs=[])
            role_names = [TARGETS, SNAPSHOT, TIMESTAMP]
            self.assertTrue(
                all(getattr(roles, n).signed.version == 2 for n in role_names)
            )
            self.assertTrue(Roles._publish_metadata.called)  # noqa

    def test__publish_metadata(self):
        with patch.multiple(Roles, sign_role=Mock(), persist_role=Mock()):
            # prepare
            roles = Roles(dir_path=self.temp_dir_path)
            roles.encrypted = []
            # test
            role_names = TOP_LEVEL_ROLE_NAMES
            roles._publish_metadata(role_names=role_names, keys_dirs=[])
            self.assertTrue(Roles.sign_role.called)  # noqa
            self.assertTrue(Roles.persist_role.called)  # noqa
