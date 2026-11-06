"""Bounded final synthetic laboratory demonstration."""
import asyncio
import tempfile
from pathlib import Path
from swarmguard.storage.database import Database
from swarmguard.research.laboratory import Laboratory
from swarmguard.research.experiment_designer import ExperimentConfig
from swarmguard.research.export import export_bundle, verify_bundle

async def main() -> None:
    with tempfile.TemporaryDirectory(prefix='swarmguard-final-') as root:
        db=Database()
        try:
            lab=Laboratory(db);lab.datasets.seed()
            for mode in ('SCRIPTED','MOCK_AUTONOMOUS'):
                record=await lab.run(lab.prepare(ExperimentConfig(dataset='swarmguard_autonomous',mode=mode))['experiment_id'])
                assert record['status']=='COMPLETED',record['error']
                path=export_bundle(record,Path(root)/record['experiment_id'])
                assert verify_bundle(path)['valid']
                print(mode,record['metrics']['accounting'])
                for row in record['rows']:
                    print(row['dataset_scenario_id'],row['predictions'],row['feedback'])
            print('Final laboratory demonstration check: PASS')
        finally: db.close()

if __name__=='__main__': asyncio.run(main())
