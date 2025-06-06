# pylint: disable=locally-disabled, missing-module-docstring, missing-class-docstring, missing-function-docstring, wrong-import-position

import sys
import os
import tempfile
import unittest
import ic
import gzip
import json
import shutil

# The test needs to have the module in its sys path, so we traverse
# up until we find the pocket_ic package.
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pocket_ic import PocketIC, SubnetKind, SubnetConfig


class PocketICTests(unittest.TestCase):
    def test_create_canister_with_id(self):
        pic = PocketIC(SubnetConfig(nns=True))
        canister_id = ic.Principal.from_str("rwlgt-iiaaa-aaaaa-aaaaa-cai")
        actual_canister_id = pic.create_canister(canister_id=canister_id)
        self.assertEqual(actual_canister_id.bytes, canister_id.bytes)

        # Creating a new canister with the same ID fails.
        with self.assertRaises(ValueError) as ex:
            pic.create_canister(canister_id=canister_id)
        self.assertIn(
            "CanisterAlreadyInstalled",
            ex.exception.args[0],
        )

        # Creating a new canister with any ID works, gets a new ID.
        new_canister_id = pic.create_canister()
        self.assertNotEqual(new_canister_id.bytes, canister_id.bytes)

        # Creating a canister with an ID that is not hosted by any subnet creates a new subnet containing the canister.
        canister_id = ic.Principal.from_str("zzztf-6qaaa-aaaah-qfsaa-cai")
        self.assertEqual(len(pic.topology()), 1)
        pic.create_canister(canister_id=canister_id)
        self.assertEqual(len(pic.topology()), 2)

    def test_large_config_and_deduplication(self):
        pic = PocketIC(
            SubnetConfig(
                application=2,
                bitcoin=True,
                fiduciary=True,
                ii=True,
                nns=True,
                sns=True,
                system=3,
            )
        )
        app_subnets = [
            k for k, v in pic.topology().items() if v == SubnetKind.APPLICATION
        ]
        self.assertEqual(len(app_subnets), 2)
        nns_subnets = [k for k, v in pic.topology().items() if v == SubnetKind.NNS]
        self.assertEqual(len(nns_subnets), 1)
        bitcoin_subnets = [
            k for k, v in pic.topology().items() if v == SubnetKind.BITCOIN
        ]
        self.assertEqual(len(bitcoin_subnets), 1)
        system_subnets = [
            k for k, v in pic.topology().items() if v == SubnetKind.SYSTEM
        ]
        self.assertEqual(len(system_subnets), 3)

    def test_install_canister_on_subnet_and_get_subnet_of_canister(self):
        pic = PocketIC(SubnetConfig(nns=True, application=1))
        nns_subnet = next(k for k, v in pic.topology().items() if v == SubnetKind.NNS)
        app_subnet = next(
            k for k, v in pic.topology().items() if v == SubnetKind.APPLICATION
        )
        nns_canister = pic.create_canister(subnet=nns_subnet)
        app_canister = pic.create_canister(subnet=app_subnet)

        self.assertEqual(pic.get_subnet(nns_canister).bytes, nns_subnet.bytes)
        self.assertEqual(pic.get_subnet(app_canister).bytes, app_subnet.bytes)

    def test_set_get_stable_memory_no_compression(self):
        pic = PocketIC()
        canister_id = pic.create_canister()
        pic.add_cycles(canister_id, 20_000_000_000_000)
        pic.install_code(canister_id, b"\x00\x61\x73\x6d\x01\x00\x00\x00", [])

        data = b"This will be stored in stable memory."
        pic.set_stable_memory(canister_id, data)
        memory = pic.get_stable_memory(canister_id)[: len(data)]
        self.assertEqual(memory, data)

    def test_set_get_stable_memory_with_compression(self):
        pic = PocketIC()
        canister_id = pic.create_canister()
        pic.add_cycles(canister_id, 20_000_000_000_000)
        pic.install_code(canister_id, b"\x00\x61\x73\x6d\x01\x00\x00\x00", [])

        text = b"This will be compressed and sent, the server will decompress it."
        compressed = gzip.compress(text)

        pic.set_stable_memory(canister_id, compressed, compression="gzip")
        memory = pic.get_stable_memory(canister_id)[: len(text)]
        self.assertEqual(memory, text)

    def test_time(self):
        pic = PocketIC()
        pic.set_time(1704067199999999999)
        self.assertEqual(
            pic.get_time(),
            {"nanos_since_epoch": 1704067199999999999},
        )
        pic.advance_time(1_000_000_000)
        self.assertEqual(
            pic.get_time(),
            {"nanos_since_epoch": 1704067200999999999},
        )

    def test_delete_instance(self):
        pic = PocketIC()
        server = pic.server
        initial_num = server.list_instances().count("Deleted")
        del pic
        self.assertEqual(server.list_instances().count("Deleted"), initial_num + 1)

    def test_tick(self):
        pic = PocketIC()
        self.assertEqual(pic.tick(), None)

    def test_get_root_key(self):
        pic = PocketIC()
        self.assertTrue(pic.get_root_key() is None)

        pic = PocketIC(SubnetConfig(nns=True))
        self.assertTrue(pic.get_root_key() is not None)

    def test_canister_exists(self):
        pic = PocketIC()
        canister_id = pic.create_canister()
        self.assertEqual(pic.check_canister_exists(canister_id), True)

    def test_canister_exists_negative(self):
        pic = PocketIC()
        canister_id = ic.Principal.anonymous()
        self.assertEqual(pic.check_canister_exists(canister_id), False)

    def test_call_empty_canister_throws(self):
        pic = PocketIC()
        canister_id = pic.create_canister()
        with self.assertRaises(ValueError) as ex:
            pic.query_call(canister_id, "foo", b"")
        self.assertIn(
            "CanisterWasmModuleNotFound",
            ex.exception.args[0],
        )

    def test_call_nonexistent_canister(self):
        pic = PocketIC()
        canister_id = ic.Principal.anonymous()
        with self.assertRaises(ConnectionError) as ex:
            pic.query_call(canister_id, "foo", b"")
        self.assertIn(
            "does not belong to any subnet",
            ex.exception.args[0],
        )

    def test_cycles_balance(self):
        pic = PocketIC()
        canister_id = pic.create_canister()
        initial_balance = pic.get_cycles_balance(canister_id)
        pic.add_cycles(canister_id, 6_666)
        self.assertEqual(pic.get_cycles_balance(canister_id), initial_balance + 6_666)

    def test_store_restore_state(self):
        # create a PocketIC instance with a state directory
        tmp_dir = tempfile.mkdtemp()
        config = SubnetConfig(application=1, nns=True, state_dir=tmp_dir)
        pic = PocketIC(config)

        # create a canister on app subnet
        app_subnet = next(
            k for k, v in pic.topology().items() if v == SubnetKind.APPLICATION
        )
        canister_id = pic.create_canister(subnet=app_subnet)
        pic.add_cycles(canister_id, 20_000_000_000_000)
        pic.install_code(canister_id, b"\x00\x61\x73\x6d\x01\x00\x00\x00", [])

        # set stable memory of the canister
        pic.set_stable_memory(canister_id, b"Hello world!")
        stable_mem = pic.get_stable_memory(canister_id)
        self.assertTrue(stable_mem.startswith(b"Hello world!"))

        # delete the PocketIC instance
        del pic

        # create a new PocketIC instance with the same state, subnets and canister states stay
        config2 = SubnetConfig(
            state_dir=tmp_dir,
        )
        pic2 = PocketIC(config2)
        stable_mem2 = pic2.get_stable_memory(canister_id)
        self.assertEqual(stable_mem2, stable_mem)
        self.assertEqual(len(pic2.topology()), 2)

        del pic2

        # create a new PocketIC instance with only the app subnet copied over
        config3 = SubnetConfig()

        # get the app subnet's folder from the state directory
        with open(os.path.join(tmp_dir, "topology.json"), "r") as f:
            dirs = json.load(f)["subnet_configs"].items()

        # find the app subnet's folder
        app_subnet_key = [
            k
            for k, v in dirs
            if v["subnet_config"]["subnet_kind"] == SubnetKind.APPLICATION.value
        ][0]

        config3.add_subnet_with_state(
            SubnetKind.APPLICATION,
            os.path.join(tmp_dir, app_subnet_key),
        )

        # app subnet canister stays, NNS subnet is not there
        pic3 = PocketIC(config3)
        stable_mem3 = pic3.get_stable_memory(canister_id)
        self.assertEqual(stable_mem3, stable_mem2)
        self.assertEqual(len(pic3.topology()), 1)

        # clean up
        shutil.rmtree(tmp_dir)


if __name__ == "__main__":
    unittest.main()
