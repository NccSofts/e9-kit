"""Acceptance tests da op de exemplo: os requirements da spec, escritos antes do run.

Cada teste leva no nome o requirement que prova (`test_fr_NNN_`); o harness recusa a op se um
requirement ficar sem teste. O import fica DENTRO de cada teste: antes do agent, `slug` não
existe, e no topo do arquivo o import quebraria a coleta (rc 9 — erro de coleta não prova nada).
"""
import unittest


class TestAcceptanceSlug(unittest.TestCase):
    def test_fr_001_minusculas_sem_acento(self):
        from texto import slug
        self.assertEqual(slug("Ação é Útil, de verdade!"), "acao-e-util-de-verdade")

    def test_fr_002_hifen_unico_entre_palavras(self):
        from texto import slug
        self.assertEqual(slug("a  --  b"), "a-b")

    def test_fr_003_sem_hifen_nas_pontas(self):
        from texto import slug
        self.assertEqual(slug("  -título-  "), "titulo")

    def test_fr_004_vazio_e_so_pontuacao(self):
        from texto import slug
        self.assertEqual(slug(""), "")
        self.assertEqual(slug("!!!"), "")


if __name__ == "__main__":
    unittest.main()
