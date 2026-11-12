"""Human demonstration descriptions, deliberately separate from observations."""
import json
import math
from pathlib import Path
from urllib.parse import urlsplit
SCENARIOS = [
    {'key':'query-efficiency','name':'SQL 效率退化','service':'svc-a','mode':'query','target_key':'svc-a',
     'status':'implemented_awaiting_verification','description':'业务查询变慢，观察扫描代价与请求耗时；现有告警是否覆盖以实际事件为准。'},
    {'key':'pool-pressure','name':'应用连接池紧张','service':'svc-a','mode':'pool','target_key':'svc-a',
     'status':'implemented_awaiting_verification','description':'小应用池出现等待，数据库全局仍有连接余量。'},
    {'key':'temporary-work','name':'报表临时表代价','service':'svc-b','mode':'temporary','target_key':'svc-b',
     'status':'implemented_awaiting_verification','description':'固定有界报表查询产生磁盘临时表，核对查询与业务影响。'},
    {'key':'application-queue','name':'应用排队反证','service':'svc-a','mode':'queue','target_key':'svc-a',
     'status':'implemented_awaiting_verification','description':'应用处理变慢而数据库调用保持正常，验证平台能否排除数据库原因。'},
    {'key':'retained-view','name':'长事务与历史版本','service':'svc-b','mode':'view','target_key':'svc-b',
     'status':'implemented_awaiting_verification','description':'持有一致性读并有界提交测试写入，观察长事务与历史积压。'},
    {'key':'stale-replica','name':'副本业务读陈旧','service':'svc-c','mode':'replica','target_key':'svc-c',
     'status':'implemented_awaiting_verification','description':'真实源库持续提交，副本应用受阻；联合核对复制线程、版本距离与业务数据时间。'},
]

# Demonstration context, not model input or a diagnosis. Until acceptance records
# override these texts, every alert-gap/analysis statement remains a hypothesis.
PRESENTATION = {
    'query-efficiency':('业务查询响应变慢，同样请求需要扫描更多数据。','待验证假设：单次查询尚未达到慢查询时间/频率/持续阈值，实际是否告警须核对启用宏和事件。','待平台验证：联合请求/DB耗时、扫描与返回行、语句摘要，区分SQL效率变化和其他等待。'),
    'pool-pressure':('应用请求等不到连接，出现排队或失败，数据库全局仍可能有连接余量。','待验证假设：应用池容量为6，而DB连接上限40；全局连接阈值不能直接表示小应用池的等待。','待平台验证：联合池容量、占用、获取等待、请求错误及数据库连接余量，不先认定数据库满连接。'),
    'temporary-work':('报表查询使用磁盘临时表，单个业务请求耗时上升。','待验证假设：本次短窗/低总量可能未达到官方临时表速率与持续阈值，不能等同于模板没有该告警。','待平台验证：关联请求与DB耗时、临时表速率、扫描代价和SQL摘要，核对是否有实际业务影响。'),
    'application-queue':('端到端请求变慢、队列积压，但数据库调用可能保持原速。','待验证假设：数据库指标本身正常，不应期待DB模板替代应用端到端监控；需核对应用是否已有告警。','待平台验证：用请求排队与耗时、正常DB调用和扫描代价作反证，避免把所有慢请求归因于数据库。'),
    'retained-view':('持续读事务占用旧版本，有提交写入时历史列表增长，未必已有明显锁等待。','待验证假设：短时事务/历史增长不一定触发当前模板阈值；需要区分现有告警与潜在健康风险。','待平台验证：联合事务年龄、历史列表、提交活动及业务耗时；证据不足时只报告待核验风险。'),
    'stale-replica':('应用读到落后版本，源库仍有提交，复制线程显示运行也不等于业务数据新鲜。','待验证假设：复制可能尚未达到当前延迟持续阈值；若已有复制告警，只能算补充业务影响解释。','待平台验证：关联真实两端版本/数据时间距离、复制线程与延迟、应用读耗时，不靠场景名称判断。'),
}


def safe_validation_evidence(value,config):
    if not isinstance(value,dict):return None
    result={k:str(value[k])[:100] for k in ('checked_at','report_id') if value.get(k)}
    kind=value.get('comparison_kind')
    result['comparison_kind']=kind if kind in {'not_triggered_in_this_window','already_alerted','not_checked'} else 'not_checked'
    try:
        report=urlsplit(value.get('report_url') or '');base=urlsplit(config.get('platform_public_url') or '')
        if (report.scheme in {'http','https'} and report.netloc==base.netloc and report.scheme==base.scheme
                and report.hostname and not report.username and not report.password):
            result['report_url']=report.geturl()
    except ValueError:pass
    measurements=[]
    raw=value.get('measurements');raw=raw if isinstance(raw,list) else []
    for row in raw[:6]:
        if not isinstance(row,dict):continue
        clean={k:str(row.get(k) or '')[:100] for k in ('label','unit')}
        for field in ('before','during','after'):
            v=row.get(field);clean[field]=v if type(v) in (int,float) and math.isfinite(v) and abs(v)<1e18 else None
        measurements.append(clean)
    result['measurements']=measurements
    return result


def public_catalog(config):
    enabled=set(config.get('services',{}))
    # Acceptance records are maintained by the verifier, never inferred from ON.
    # Read each time so an accepted scene becomes visible without a controller restart.
    accepted={}
    if config.get('verification_path'):
        try:accepted=json.loads(Path(config['verification_path']).read_text(encoding='utf-8-sig'))
        except (OSError,ValueError):pass
    if not isinstance(accepted,dict):accepted={}
    rows=[]
    for row in SCENARIOS:
        proof=accepted.get(row['key'],{});proof=proof if isinstance(proof,dict) else {}
        status=proof.get('status',row['status'])
        if status not in {'implemented_awaiting_verification','direct_verified','platform_verified','not_implemented'}:
            status=row['status']
        presentation=dict(zip(('business_symptom','zabbix_comparison','platform_clues'),PRESENTATION[row['key']]))
        for field in presentation:
            override=proof.get(field)
            if isinstance(override,str) and override.strip():presentation[field]=override[:700]
        rows.append(dict((k,v) for k,v in row.items() if k not in ('service','mode')) |
            presentation|{'status':status,'available':row['status']!='not_implemented' and row['service'] in enabled,
                'validation_evidence':safe_validation_evidence(proof.get('validation_evidence'),config)})
    return rows
