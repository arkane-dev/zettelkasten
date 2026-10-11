"""Run the graphrag CLI with one extra workflow: community reports for the top levels only.

GraphRAG writes a report for every community at every level. The zettelkasten only turns
levels 0 and 1 into notes, so the deeper reports are wasted LLM calls.
create_community_reports_top filters communities to level <= TOP_LEVEL (default 1), then runs
GraphRAG's own text-report step. Select it with `workflows:` in settings.yaml.

Usage: python index_top.py index --root rag-hybrid --method fast
"""
import logging, os

from graphrag.cli.main import app
from graphrag.data_model.data_reader import DataReader
from graphrag.index.typing.workflow import WorkflowFunctionOutput
from graphrag.index.workflows.create_community_reports_text import create_community_reports_text
from graphrag.index.workflows.factory import PipelineFactory
from graphrag_llm.completion import create_completion
from graphrag.cache.cache_key_creator import cache_key_creator

logger = logging.getLogger(__name__)
TOP_LEVEL = int(os.environ.get('TOP_LEVEL', 1))


async def create_community_reports_top(config, context):
    logger.info('Workflow started: create_community_reports_top (levels 0..%d)', TOP_LEVEL)
    reader = DataReader(context.output_table_provider)
    entities = await reader.entities()
    communities = await reader.communities()
    text_units = await reader.text_units()
    top = communities[communities['level'] <= TOP_LEVEL].reset_index(drop=True)
    logger.info('Reporting on %d of %d communities', len(top), len(communities))

    rc = config.community_reports
    model = create_completion(
        config.get_completion_model_config(rc.completion_model_id),
        cache=context.cache.child(rc.model_instance_name),
        cache_key_creator=cache_key_creator,
    )
    output = await create_community_reports_text(
        entities, top, text_units, context.callbacks,
        model=model, tokenizer=model.tokenizer, prompt=rc.resolved_prompts().text_prompt,
        max_input_length=rc.max_input_length, max_report_length=rc.max_length,
        num_threads=config.concurrent_requests, async_type=config.async_mode,
    )
    await context.output_table_provider.write_dataframe('community_reports', output)
    logger.info('Workflow completed: create_community_reports_top')
    return WorkflowFunctionOutput(result=output)


PipelineFactory.register('create_community_reports_top', create_community_reports_top)

if __name__ == '__main__':
    app()
