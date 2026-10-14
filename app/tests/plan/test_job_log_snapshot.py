from app.services.plans.decomposition_jobs import PlanDecompositionJob


def test_log_arriving_during_serialization_is_retained_for_next_snapshot():
    job = PlanDecompositionJob(job_id='snapshot',plan_id=1,task_id=2,mode='task_chain')

    class FirstEvent:
        appended = False
        def to_payload(self):
            if not self.appended:
                self.appended = True
                job.append_log('info','Arrived while serializing')
            return {'message':'Original event'}

    first = FirstEvent()
    job.logs.append(first)
    assert 'logs' not in job.to_payload(include_logs=False)
    assert not first.appended
    assert job.to_payload()['logs'] == [{'message':'Original event'}]
    next_logs = job.to_payload()['logs']
    assert len(next_logs)==2 and next_logs[-1]['message']=='Arrived while serializing'
