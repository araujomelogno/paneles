"""La separación de stores es parte del diseño: la config la hace cumplir."""

import pytest

from panel_api import config

BASE = {
    "DSN_LOCAL": "postgresql://u:p@10.0.0.1:5432/paneles_local",
    "DSN_REMOTO": "postgresql://u:p@10.0.0.2:5432/paneles_remoto",
}


def test_carga_los_dos_dsn():
    cfg = config.cargar(BASE)
    assert cfg.dsn_local != cfg.dsn_remoto
    assert cfg.proveedor_embeddings == "voyage"
    assert cfg.dims_embeddings == 1024


def test_falla_si_los_dos_stores_apuntan_a_la_misma_base():
    mismo = {"DSN_LOCAL": BASE["DSN_LOCAL"], "DSN_REMOTO": BASE["DSN_LOCAL"]}
    with pytest.raises(config.ErrorConfig, match="misma base"):
        config.cargar(mismo)


def test_falla_aunque_cambien_las_credenciales_pero_sea_la_misma_base():
    con_otro_usuario = {
        "DSN_LOCAL": "postgresql://uno:x@10.0.0.1:5432/paneles",
        "DSN_REMOTO": "postgresql://otro:y@10.0.0.1:5432/paneles",
    }
    with pytest.raises(config.ErrorConfig, match="misma base"):
        config.cargar(con_otro_usuario)


@pytest.mark.parametrize("faltante", ["DSN_LOCAL", "DSN_REMOTO"])
def test_falla_si_falta_un_dsn(faltante):
    entorno = dict(BASE)
    entorno.pop(faltante)
    with pytest.raises(config.ErrorConfig, match=faltante):
        config.cargar(entorno)
