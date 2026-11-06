"""Only the trusted local host or authenticated researcher can designate a baseline."""
from __future__ import annotations
from typing import Any
from swarmguard.clock import Clock
from swarmguard.storage.repositories import PlatformRepository


class BaselineManager:
    def __init__(self, repository: PlatformRepository, clock: Clock) -> None:
        self.repo,self.clock = repository,clock
        self.__authority = object()

    def host_authority(self) -> object:
        """Host-only capability: never provide this manager or token to an agent."""
        return self.__authority

    def designate(self, experiment_id: str, *, authority: object, principal: str) -> dict[str, Any]:
        if authority is not self.__authority:
            raise PermissionError('trusted researcher authority required; an agent ID is not approval')
        stored = self.repo.get('framework_evaluation',experiment_id)
        if not stored:
            raise KeyError(experiment_id)
        evaluation = stored[0]
        if evaluation['status'] != 'COMPLETED' or evaluation['metrics']['failed_trials'] or evaluation.get('unrun_trials'):
            raise ValueError('incomplete or failed evaluations cannot be official baselines')
        if evaluation['metrics']['prohibited_outcomes']:
            raise ValueError('this baseline policy refuses prohibited executions')
        bid = self.repo.db.next_id('baseline')
        record = {'baseline_id':bid,'experiment_id':experiment_id,'application':evaluation['config']['application'],
            'application_version':evaluation['config']['application_version'],'framework':evaluation['config']['framework'],
            'framework_version':evaluation['framework_version'],'configuration_hash':evaluation['configuration_hash'],
            'prompt_hash':evaluation['prompt_hash'],'tool_schema_hash':evaluation['tool_schema_hash'],
            'authorization_policy_version':evaluation['policy_version'],'benchmark_version':evaluation['suite_version'],
            'created_at':self.clock.now().isoformat(),'approved_by':principal,'evaluation':evaluation}
        self.repo.create('baseline',bid,record,actor=principal,occurred_at=self.clock.now().isoformat())
        return record
