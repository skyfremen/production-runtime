"""Zodiac analytics profile; collection and warehouse transport are shared with Drama."""
from __future__ import annotations
import json
import os
import re
from pathlib import Path
import analytics as shared
from analytics_remote import GitHubRepository, run_remote
from .contract import CHANNEL, REPOSITORY
from .content import FORMATS

CONTENT_ID=re.compile(r'^za-[a-z0-9-]{8,64}$')
WAREHOUSE='skyfremen/zodiac-analytics-data'
MAX_PROJECTION_BYTES=16000


def read_requests(root):
    for path in sorted((Path(root)/'content/requests').glob('*.json')):
        doc=shared.read_json(path)
        if not isinstance(doc,dict) or not isinstance(doc.get('items'),list):
            raise RuntimeError('Zodiac analytics request schema invalid')
        yield doc


def creative_map(root):
    out={}
    for request in read_requests(root):
        for item in request['items']:
            cid=str(item.get('content_id','')); winner=item.get('zodiac') or {}
            if not CONTENT_ID.fullmatch(cid) or winner.get('format') not in FORMATS:continue
            title=str(winner.get('title','')); words=len(title.split())
            out[cid]={'title':title,'category':winner['format'],'format':winner['format'],
                'hook':title,'headline_length':'3_6_words' if words<=6 else '7_10_words' if words<=10 else '11_18_words',
                'rows':winner.get('rows',[]),'editorial_scores':winner.get('editorial_scores',{})}
    return out


def eligible_result(root,result,creative):
    cid=result.get('content_id')
    return (result.get('verified') is True and result.get('status') in {'scheduled','published'} and cid in creative
        and not any((Path(root)/'content'/folder/f'{cid}.json').exists() for folder in ('abandonments','slot-cancellations')))


def verify_channel(root,token):
    response=shared.youtube_data('channels',{'part':'id,snippet','mine':'true'},token)
    items=response.get('items',[])
    if len(items)!=1 or str((items[0].get('snippet') or {}).get('customUrl','')).casefold()!=CHANNEL['handle'].casefold():
        raise RuntimeError('Zodiac analytics credentials resolve to the wrong channel')
    channel_id=items[0].get('id','')
    if not re.fullmatch(r'UC[A-Za-z0-9_-]{22}',channel_id):raise RuntimeError('Zodiac analytics channel ID invalid')
    expected={request.get('publication',{}).get('channel_id') for request in read_requests(root) if request.get('publication',{}).get('enabled') is True}
    if expected and expected!={channel_id}:raise RuntimeError('Zodiac analytics request channel differs from authenticated channel')
    return channel_id


def numeric(value):
    import math
    return type(value) in (int,float) and math.isfinite(value)


def enrich_summary(summary,rows,current,root):
    latest={str(v.get('content_id')):v for v in current.get('videos',[]) if isinstance(v,dict)}
    for row in rows:
        item=latest.get(row['content_id'],{}); creative=item.get('creative') or {}; metrics=item.get('metrics') or {}
        mature=float(item.get('age_hours',0))>=72
        row['format']=creative.get('format') or row.get('category','unknown')
        row['headline_length']=creative.get('headline_length','unknown')
        row['answer_examples']=[{'label':str(r.get('label',''))[:48],'answer':str(r.get('answer',''))[:68]} for r in creative.get('rows',[])[:3] if isinstance(r,dict)]
        for key in ('engaged_views','average_view_duration','likes','comments','shares','subscribers_gained'):
            row[key]=metrics.get(key) if mature else None
        # This matched-source ratio counts starts/replays; it is not Studio stayed-to-watch.
        row['mature_shorts_ratio']=row.get('shorts_source_engaged_view_rate_percentage') if mature else None
    def grouped(key):
        groups=shared.group_summary(rows,key)
        for name,metrics in groups.items():
            items=[r for r in rows if str(r.get(key) or 'unknown')==name]
            metrics['mature_shorts_sample']=sum(numeric(r.get('mature_shorts_ratio')) for r in items)
            metrics['median_shorts_source_engaged_view_rate_percentage']=shared.med([r.get('mature_shorts_ratio') for r in items])
            for source,target in [('engaged_views','engaged_views'),('average_view_duration','average_view_duration'),('likes','likes'),('comments','comments')]:
                metrics['median_'+target]=shared.med([r.get(source) for r in items])
            metrics['views_72h_thresholds']={str(n):sum(numeric(r.get('views_72h')) and r['views_72h']>=n for r in items) for n in (100,500,1000)}
        return groups
    for key in ('category_performance','tone_performance','lead_gender_performance','hook_type_performance','trend_performance','trend_topic_performance','low_sample_categories'):
        summary.pop(key,None)
    summary['viewer_response_baseline']={'shorts_source_engaged_view_rate_percentage':shared.med([r.get('mature_shorts_ratio') for r in rows]),'average_view_percentage':shared.med([r.get('retention') for r in rows])}
    summary['lane']='zodiac';summary['format_performance']=grouped('format');summary['headline_length_performance']=grouped('headline_length')
    summary['metric_notes']={'shorts_source_engaged_view_rate_percentage':'Matched Shorts traffic-source engagedViews/views ratio; not Studio stayed-to-watch. Starts and replays affect the denominator.',
        'average_view_percentage':'Values over 100% may reflect repeated viewing; do not equate them with full-video completion.',
        'checkpoints':'Nearest observed age, not guaranteed exact checkpoint; API reporting can lag.',
        'bulk_reports':'Raw Reporting CSVs are stored and indexed; compact decisions use targeted Analytics API metrics.'}
    for field in ('top_examples','weak_retention_examples'):
        summary[field]=[example(next((r for r in rows if r['content_id']==v['content_id']),v)) for v in summary.get(field,[])]
    patterns=summary.get('retention_patterns',{})
    if 'median_mid_story_retention' in patterns:patterns['median_mid_video_retention']=patterns.pop('median_mid_story_retention')
    return summary


def example(row):
    keys=('content_id','title','format','headline_length','duration_seconds','views_2h','views_6h','views_24h','views_72h','views_7d','retention','average_view_duration','mature_shorts_ratio','answer_examples','checkpoint_observations')
    return {key:row.get(key) for key in keys}


def planner_projection(summary):
    base=shared.planner_projection(summary)
    base['lane']='zodiac'
    minimum=base['learning']['minimum_pattern_sample'];patterns=[]
    baseline=summary.get('viewer_response_baseline',{})
    for dimension,key in (('format','format_performance'),('headline_length','headline_length_performance')):
        for value,metrics in summary.get(key,{}).items():
            checkpoint=next((c for c in ('7d','72h') if metrics.get('sample_'+c,0)>=minimum),None)
            response=metrics.get('median_shorts_source_engaged_view_rate_percentage')
            retention=metrics.get('median_average_view_percentage')
            if not checkpoint:continue
            if metrics.get('mature_shorts_sample',0)<minimum:response=None
            if metrics.get('mature_sample',0)<minimum:retention=None
            if not numeric(response) and not numeric(retention):continue
            patterns.append({'dimension':dimension,'value':value,'sample_size':metrics['sample_size'],
                'mature_sample':metrics.get('mature_sample',0),'mature_shorts_sample':metrics.get('mature_shorts_sample',0),
                'evidence_checkpoint':checkpoint,'checkpoint_sample':metrics['sample_'+checkpoint],
                'evidence_median_views':metrics.get('median_views_'+checkpoint),
                'shorts_source_engaged_view_rate_percentage':response,'average_view_percentage':retention,
                'average_view_duration':metrics.get('median_average_view_duration')})
    patterns.sort(key=lambda p:(-(p['shorts_source_engaged_view_rate_percentage'] if numeric(p['shorts_source_engaged_view_rate_percentage']) else -1),-(p['average_view_percentage'] if numeric(p['average_view_percentage']) else -1),p['dimension'],p['value']))
    def weaker(pattern):
        return any(numeric(pattern.get(key)) and numeric(baseline.get(key)) and pattern[key]<baseline[key]
            for key in ('shorts_source_engaged_view_rate_percentage','average_view_percentage'))
    supported=[p for p in patterns if not weaker(p)]
    weak=[p for p in patterns if weaker(p)]
    base['creative_signals']={'soft_evidence_only':True,'supported_patterns':supported[:6],
        'weak_patterns':list(reversed(weak[-4:])), 'comparison_baseline':baseline,
        'metric_note':summary.get('metric_notes',{}).get('shorts_source_engaged_view_rate_percentage'),
        'top_examples':summary.get('top_examples',[])[:3], 'weak_retention_examples':summary.get('weak_retention_examples',[])[:2]}
    base['distribution_signals']['publish_slots_sgt']=list(summary.get('publish_slot_performance_sgt',{}).items())[:6]
    # Preserve a compact context contract even if optional audience strings are large.
    while len((json.dumps(base,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode())>MAX_PROJECTION_BYTES:
        for container,key in ((base['creative_signals'],'top_examples'),(base['creative_signals'],'weak_retention_examples'),(base['audience_signals'],'dominant_countries'),(base['audience_signals'],'dominant_traffic_sources'),(base['distribution_signals'],'publish_slots_sgt'),(base,'warnings')):
            if container.get(key):container[key].pop();break
        else:raise RuntimeError('Zodiac planner analytics projection exceeds size bound')
    return base


PROFILE=shared.AnalyticsProfile(lane='zodiac',content_id_re=CONTENT_ID,result_glob='za-*.json',
    credential_names=('ZODIAC_CLIENT_ID','ZODIAC_CLIENT_SECRET','ZODIAC_REFRESH_TOKEN'),
    warehouse_repository=WAREHOUSE,schema_name='wacky-astrology-youtube-analytics',creative_loader=creative_map,
    result_filter=eligible_result,channel_guard=verify_channel,summary_adapter=enrich_summary,projection_builder=planner_projection,
    optional_error_details=True)


def run(planner_root,warehouse_root,collected_at=None):
    return shared.run(planner_root,warehouse_root,collected_at,profile=PROFILE)


def remote_main():
    token=os.environ.get('ZODIAC_STATE_TOKEN','').strip()
    if not token:raise RuntimeError('Missing required secret ZODIAC_STATE_TOKEN')
    return run_remote(GitHubRepository(REPOSITORY,token,'planner'),GitHubRepository(WAREHOUSE,token,'warehouse'),runner=run)


if __name__=='__main__':
    remote_main()
