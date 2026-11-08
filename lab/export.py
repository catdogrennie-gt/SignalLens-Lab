"""Allowlist-only public export; identity/config/connection data never copied."""
import json
from pathlib import Path

def public_manifest(raw):
    fields=('source_mode','scenario','started_at','ended_at','injected_action','status','cleanup','dataset_before','dataset_after','worker_outcomes','errors')
    result={key:raw[key] for key in fields if key in raw}
    result['artifact_type']='archived_real_lab_measurements'
    result['not_production']=True
    result['limitations']=[
      'Recorded live Lab observations, not live values at the time this file is read.',
      'VM CPU/disk aggregate the guest, including other services.',
      'Probe latency includes authentication and SELECT 1, not pure network RTT.',
      'QPS includes collector traffic. Original runs sampled initial recovery immediately after negative control; its very short delta exaggerates sampling overhead. Raw values preserved; do not interpret this as fault throughput.',
      'Synthetic deterministic orders business data; real telemetry and fault symptoms.',
      'No network fault or production use claimed.'
    ]
    result['observations']=[{key:sample[key] for key in ('timestamp','clock','phase','values','sender','sender_error') if key in sample} for sample in raw['observations']]
    result['provenance']={'database':'MySQL performance_schema/status on isolated lab_orders target','host':'Linux VM /proc/stat and /proc/diskstats','network':'probe container -> dedicated MySQL target','zabbix':'trapper sender; numeric history and actual text snapshots','units':{'cpu':'percent','probe_latency':'milliseconds','query_rate':'queries/second','disk_rate':'bytes/second','sql_digest_timers':'picoseconds, cumulative'}}
    return result

def main():
    parser=__import__('argparse').ArgumentParser(); parser.add_argument('input'); parser.add_argument('output'); args=parser.parse_args()
    output=Path(args.output); output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(public_manifest(json.loads(Path(args.input).read_text())),indent=2),encoding='utf-8')
if __name__=='__main__': main()
