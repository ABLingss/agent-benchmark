import unittest
from router import Backend, Router


class PublicAcceptance(unittest.TestCase):
    def test_affinity(self):
        router = Router([Backend("a", "http://localhost/v1"),
                         Backend("b", "http://localhost/v1", prefixes=[("user", "hello", "world")])])
        backend, affinity = router.reserve(("user", "hello", "there"), "cache_aware")
        self.assertEqual((backend.id, affinity), ("b", 2))
        router.release(backend, (), False)

    def test_full_backend_skipped(self):
        router = Router([Backend("a", "http://localhost/v1", in_flight=2, prefixes=[("p", "q")]),
                         Backend("b", "http://localhost/v1")])
        backend, _ = router.reserve(("p", "q"), "cache_aware")
        self.assertEqual(backend.id, "b")
        router.release(backend, (), False)

    def test_fallback(self):
        router = Router([Backend("a", "http://localhost/v1"), Backend("b", "http://localhost/v1")])
        ids = []
        for _ in range(3):
            backend, _ = router.reserve(("single",), "cache_aware")
            ids.append(backend.id)
            router.release(backend, (), False)
        self.assertEqual(ids, ["a", "b", "a"])


if __name__ == "__main__":
    unittest.main()
