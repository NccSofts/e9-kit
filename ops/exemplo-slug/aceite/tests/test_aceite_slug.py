"""Aceite da tarefa de exemplo: os critérios de pronto da OPORD, escritos antes do voo.

O import fica DENTRO de cada teste. Antes do agente, `slug` não existe: no topo do arquivo, o
import quebraria a coleta (rc 9, erro de coleta não prova nada); aqui, cada teste falha sozinho.
"""
import unittest


class TestAceiteSlug(unittest.TestCase):
    def test_minusculas_sem_acento(self):
        from texto import slug
        self.assertEqual(slug("Ação é Útil, de verdade!"), "acao-e-util-de-verdade")

    def test_hifen_unico_entre_palavras(self):
        from texto import slug
        self.assertEqual(slug("a  --  b"), "a-b")

    def test_sem_hifen_nas_pontas(self):
        from texto import slug
        self.assertEqual(slug("  -título-  "), "titulo")

    def test_vazio_e_so_pontuacao(self):
        from texto import slug
        self.assertEqual(slug(""), "")
        self.assertEqual(slug("!!!"), "")


if __name__ == "__main__":
    unittest.main()
