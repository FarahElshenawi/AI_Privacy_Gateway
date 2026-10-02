"""Real tests for the Vault — bijective constraint, collision, expiry, persistence."""
import pytest
import os
import tempfile
import time
from app.vault.store import VaultStore
from app.vault.collision import CollisionError, generate_unique_fake, generate_fake_name


class TestBijective:
    def setup_method(self):
        self.vault = VaultStore()

    def test_same_fake_maps_to_same_real(self):
        self.vault.add_mapping("conv1", "James Walsh", "Farah Ahmed", "PERSON")
        self.vault.add_mapping("conv1", "James Walsh", "Farah Ahmed", "PERSON")
        entry = self.vault.lookup_by_fake("conv1", "James Walsh")
        assert entry is not None
        assert entry.real_value == "Farah Ahmed"

    def test_collision_different_real_raises(self):
        self.vault.add_mapping("conv1", "James Walsh", "Farah Ahmed", "PERSON")
        with pytest.raises(CollisionError):
            self.vault.add_mapping("conv1", "James Walsh", "John Smith", "PERSON")

    def test_reverse_lookup_works(self):
        self.vault.add_mapping("conv1", "James Walsh", "Farah Ahmed", "PERSON")
        entry = self.vault.lookup_by_real("conv1", "Farah Ahmed")
        assert entry is not None
        assert entry.fake_value == "James Walsh"

    def test_different_conversations_are_isolated(self):
        self.vault.add_mapping("conv1", "James Walsh", "Farah Ahmed", "PERSON")
        self.vault.add_mapping("conv2", "Robert Hayes", "Farah Ahmed", "PERSON")
        e1 = self.vault.lookup_by_real("conv1", "Farah Ahmed")
        e2 = self.vault.lookup_by_real("conv2", "Farah Ahmed")
        assert e1.fake_value == "James Walsh"
        assert e2.fake_value == "Robert Hayes"

    def test_restore_text_replaces_all_fakes(self):
        self.vault.add_mapping("conv1", "James Walsh", "Farah Ahmed", "PERSON")
        self.vault.add_mapping("conv1", "j.walsh@fake.com", "farah@example.com", "EMAIL")
        text = "Hi James Walsh, email j.walsh@fake.com"
        restored = self.vault.restore_text("conv1", text)
        assert "Farah Ahmed" in restored
        assert "farah@example.com" in restored
        assert "James Walsh" not in restored

    def test_restore_does_not_touch_other_conversations(self):
        self.vault.add_mapping("conv1", "James Walsh", "Farah Ahmed", "PERSON")
        self.vault.add_mapping("conv2", "Robert Hayes", "John Smith", "PERSON")
        text = "Hi James Walsh and Robert Hayes"
        restored = self.vault.restore_text("conv1", text)
        assert "Farah Ahmed" in restored
        assert "Robert Hayes" in restored

    def test_longer_fakes_replaced_first(self):
        self.vault.add_mapping("conv1", "James Walsh", "Farah Ahmed", "PERSON")
        self.vault.add_mapping("conv1", "James", "Farah", "PERSON")
        text = "James Walsh is here"
        restored = self.vault.restore_text("conv1", text)
        assert "Farah Ahmed" in restored
        assert "Farah Walsh" not in restored


class TestCollisionDetection:
    def test_generate_unique_fake_no_collision(self):
        existing = {"James Walsh", "John Smith"}
        fake = generate_unique_fake(existing, generate_fake_name)
        assert fake not in existing

    def test_collision_error_on_duplicate(self):
        vault = VaultStore()
        vault.add_mapping("conv1", "James Walsh", "Farah Ahmed", "PERSON")
        with pytest.raises(CollisionError):
            vault.add_mapping("conv1", "James Walsh", "Different", "PERSON")


class TestExpiry:
    def test_expired_entry_not_returned(self):
        vault = VaultStore()
        vault.add_mapping("conv1", "James", "Farah", "PERSON", ttl_hours=0.00001)
        time.sleep(0.5)
        assert vault.lookup_by_fake("conv1", "James") is None

    def test_non_expired_entry_returned(self):
        vault = VaultStore()
        vault.add_mapping("conv1", "James", "Farah", "PERSON", ttl_hours=24)
        assert vault.lookup_by_fake("conv1", "James") is not None

    def test_cleanup_expired_removes_entries(self):
        vault = VaultStore()
        vault.add_mapping("conv1", "James", "Farah", "PERSON", ttl_hours=0.00001)
        vault.add_mapping("conv1", "John", "Smith", "PERSON", ttl_hours=24)
        time.sleep(0.5)
        removed = vault.cleanup_expired()
        assert removed == 1
        assert vault.lookup_by_fake("conv1", "James") is None
        assert vault.lookup_by_fake("conv1", "John") is not None


class TestPersistence:
    def test_persist_and_reload(self):
        tmpdir = tempfile.mkdtemp()
        db_path = os.path.join(tmpdir, "test_vault.json")
        vault1 = VaultStore(persistence_path=db_path)
        vault1.add_mapping("conv1", "James Walsh", "Farah Ahmed", "PERSON")
        vault2 = VaultStore(persistence_path=db_path)
        entry = vault2.lookup_by_fake("conv1", "James Walsh")
        assert entry is not None
        assert entry.real_value == "Farah Ahmed"

    def test_clear_conversation_removes_all(self):
        vault = VaultStore()
        vault.add_mapping("conv1", "James", "Farah", "PERSON")
        vault.add_mapping("conv1", "j@fake.com", "f@real.com", "EMAIL")
        vault.clear_conversation("conv1")
        assert vault.lookup_by_fake("conv1", "James") is None
        assert vault.get_conversation("conv1") is None
