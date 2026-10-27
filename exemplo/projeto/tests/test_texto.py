import unittest

from texto import sem_acentos


class TestSemAcentos(unittest.TestCase):
    def test_remove_acentos_e_cedilha(self):
        self.assertEqual(sem_acentos("Ação é útil"), "Acao e util")

    def test_texto_sem_acento_fica_igual(self):
        self.assertEqual(sem_acentos("abc 123"), "abc 123")


if __name__ == "__main__":
    unittest.main()
