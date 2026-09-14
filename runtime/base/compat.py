"""Wacky Dramas V1 private/public compatibility fingerprint."""
import hashlib, json, re
CONTRACT={
 "name":"wacky-dramas-production-v1","request_version":1,"execution_version":1,"result_version":1,
 "visibility":"public","request_path":"content/requests/{content_id}.json",
 "execution_path":"content/executions/{execution_id}.json","result_path":"content/results/{content_id}.json",
 "registry_path":"data/backgrounds.json",
}
def contract_hash():
    return hashlib.sha256(json.dumps(CONTRACT,sort_keys=True,separators=(",",":")).encode()).hexdigest()
def validate_contract_hash(value):
    value=str(value or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}",value): raise ValueError("Invalid compatibility fingerprint")
    if value!=contract_hash(): raise ValueError("Incompatible execution contract")
    return value
if __name__=="__main__": print(contract_hash())
