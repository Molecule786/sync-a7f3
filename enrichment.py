"""Keep all published entity attributes, without repeating studios on every class."""
from datetime import datetime, timezone


def present(value):
    return value is not None and value != '' and value != {} and value != []


def merged(*objects):
    result={}
    for obj in objects:
        for key,value in (obj or {}).items():
            if isinstance(value,dict) and isinstance(result.get(key),dict):
                result[key]=merged(result[key],value)
            elif present(value):result[key]=value
    return result


def activities(value):
    if isinstance(value,str):return [s.strip() for s in value.split(',') if s.strip()]
    return value if isinstance(value,list) else []


def class_details(cls):
    return {'name':cls.get('name'),'description':cls.get('description'),
            'activities':activities(cls.get('activities')),'level':cls.get('level'),
            'ratings':cls.get('ratings'),'images':cls.get('images'),
            'how_to_get_there':cls.get('how_to_get_there'),'what_to_bring':cls.get('what_to_bring')}


def session_details(item, observed_at):
    teacher=item.get('teacher') or {}
    return {'teacher':teacher,'instructor_name':item.get('teacher_name') or teacher.get('name'),
            'instructor_id':teacher.get('id'),'duration_minutes':item.get('duration_minutes'),
            'is_livestream':item.get('is_livestream'),'retail_price_in_credits':item.get('retail_price_in_credits'),
            'availability':item.get('availability'),'availability_observed_at':observed_at,
            'cancellation_policy_source_code':item.get('cancellation_policy'),
            'late_cancellation_source_value':item.get('late_cancellation'),
            'tag_ids':item.get('tag_ids'),'demand_signals':item.get('demand_signals'),
            'location_override':item.get('location_override')}


def compact_checkpoint(snapshot):
    if 'enrichment' not in snapshot:return snapshot
    result=dict(snapshot)
    result['enrichment']={kind:{key:{k:v for k,v in record.items() if k!='details'}
                              for key,record in records.items()}
                          for kind,records in snapshot['enrichment'].items()}
    result['tasks']={key:{**task,'rows':[{k:v for k,v in row.items() if k!='description'}
                                        for row in task['rows']]}
                     for key,task in snapshot.get('tasks',{}).items()}
    return result


def restore_checkpoint(snapshot):
    state=snapshot.get('enrichment')
    if not state:return
    for key,record in list(state['studios'].items()):
        state['studios'][key]=studio_record(record['studio_id'],previous=record)
    for record in state['classes'].values():record['details']=class_details(record['raw_class'])
    for record in state['sessions'].values():
        record['details']=session_details(record['raw_session'],record['observed_at'])
    for task in snapshot.get('tasks',{}).values():
        for row in task['rows']:
            session=state['sessions'].get(row['event_id'])
            if session:
                cls=state['classes'][f"{session['studio_id']}:{session['class_id']}"]['raw_class']
                if cls.get('description') is not None:row['description']=cls['description']


def studio_record(identity, directory=None, schedule=None, profile=None, checked_at=None, previous=None):
    record=dict(previous or {})
    record['studio_id']=identity
    for key,value in [('raw_directory',directory),('raw_schedule',schedule),('raw_profile',profile)]:
        if value is not None:record[key]=value
    if checked_at:record['profile_checked_at']=checked_at
    value=merged(record.get('raw_directory'),record.get('raw_profile'),record.get('raw_schedule'))
    ratings=value.get('ratings') or {}
    record['details']={
        'name':value.get('name'),'alias':value.get('alias'),
        'profile_url':'https://classpass.com/studios/'+value['alias'] if value.get('alias') else None,
        'description':value.get('description'),'activities':activities(value.get('activities')),
        'address':value.get('address'),'location':value.get('location'),
        'phone':value.get('phone_number'),'website':value.get('website'),
        'timezone':value.get('tz'),'amenities':value.get('amenities'),
        'images':value.get('images'),'opening_hours':value.get('open_hours'),
        'rating_average':ratings.get('mean',value.get('rating')),
        'rating_count':(ratings.get('count') or {}).get('total'),
        'rating_count_display':ratings.get('display_total',value.get('ratingCount')),
        'social_links':{k:value.get(k) for k in ['facebook_page_url','instagram_handle','twitter','spotify_url']},
        'how_to_get_there':value.get('how_to_get_there'),'transportation':value.get('transportation'),
        'what_to_bring':value.get('what_to_bring'),'when_to_arrive':value.get('when_to_arrive'),
        'pro_tip':value.get('pro_tip'),'booking_window':value.get('booking_window'),
        'requirements':value.get('requirements'),'safety_and_cleanliness':(value.get('attributes') or {}).get('safety_and_cleanliness'),
        'featured_reviews':value.get('featuredReviews'),'subtitle':value.get('subtitle'),
    }
    return record


def capture(item, row, state, profile_cache, checked_times, observed_at):
    venue=item.get('venue') or {};cls=item.get('class') or {}
    vid=venue.get('id');cid=cls.get('id')
    if not isinstance(vid,int) or not isinstance(cid,int):
        raise ValueError('Enriched session requires studio and class identity')
    key=str(vid)
    state['studios'][key]=studio_record(vid,schedule=venue,profile=profile_cache.get(vid),
        checked_at=checked_times.get(vid,observed_at) if vid in profile_cache else None,
        previous=state['studios'].get(key))
    class_key=f'{vid}:{cid}'
    state['classes'][class_key]={'studio_id':vid,'class_id':cid,'raw_class':cls,
        'details':class_details(cls)}
    teacher=item.get('teacher') or {}
    details={'teacher':teacher,'instructor_name':item.get('teacher_name') or teacher.get('name'),
             'instructor_id':teacher.get('id'),'duration_minutes':item.get('duration_minutes'),
             'is_livestream':item.get('is_livestream'),'retail_price_in_credits':item.get('retail_price_in_credits'),
             'availability':item.get('availability'),'availability_observed_at':observed_at,
             'cancellation_policy_source_code':item.get('cancellation_policy'),
             'late_cancellation_source_value':item.get('late_cancellation'),
             'tag_ids':item.get('tag_ids'),'demand_signals':item.get('demand_signals'),
             'location_override':item.get('location_override')}
    state['sessions'][row['event_id']]={'event_id':row['event_id'],'studio_id':vid,'class_id':cid,
        'details':details,'raw_session':{k:v for k,v in item.items() if k not in ('venue','class')},
        'observed_at':observed_at}


def bundle(state, rows, include_all_studios=False):
    sessions=[state['sessions'][r['event_id']] for r in rows]
    class_keys={f"{r['studio_id']}:{r['class_id']}" for r in sessions}
    # Studio profiles are also retained when a discovered studio has no upcoming classes.
    venue_ids={str(r['studio_id']) for r in sessions}
    return {'studios':[v for k,v in state['studios'].items() if include_all_studios or k in venue_ids],
            'classes':[state['classes'][key] for key in sorted(class_keys)],'sessions':sessions}
