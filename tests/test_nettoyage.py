import pathlib, re
INTERDITS = re.compile(r"\b(paho|sparkplug|protocols\.sparkplug_b|supervisor_service|demo_service|machine_command_service|trs_service|OrdreFabrication|LotMatierePremiere)\b")
def test_aucune_reference_pharma_ou_mqtt():
    racine = pathlib.Path(__file__).resolve().parents[1] / "app"
    fautifs = [str(p) for p in racine.rglob("*.py") if INTERDITS.search(p.read_text(encoding="utf-8"))]
    assert fautifs == []
def test_app_demarre(client):
    assert client.get("/api/v1/health").status_code == 200
def test_endpoints_demo_supprimes(client, auth_headers):
    assert client.post("/api/v1/auth/demo", headers=auth_headers).status_code == 404
    assert client.post("/api/v1/auth/demo/reset-usine", headers=auth_headers).status_code == 404
