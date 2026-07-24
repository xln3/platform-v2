from datetime import timedelta

from temporalio import workflow


@workflow.defn
class PlatformHealthWorkflow:
    @workflow.run
    async def run(self, message: str) -> str:
        await workflow.sleep(timedelta(milliseconds=10))
        return f"geo-platform-v2:{message}"
