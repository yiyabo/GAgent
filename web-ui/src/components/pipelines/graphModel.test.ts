import { describe, expect, it } from 'vitest';
import {
  addNode,
  autoLayout,
  connect,
  connectionProblem,
  disconnect,
  edgeId,
  freeSpot,
  normalizeGraph,
  parseEdgeId,
  positionsOf,
  removeNode,
  specIndex,
  toFlow,
} from './graphModel';
import { TEST_CATALOG, chainGraph } from './testCatalog';

const specs = specIndex(TEST_CATALOG);
const spec = (type: string) => specs.get(type)!;
const ref = (node: string, port: string) => ({ node, port });

describe('editing a graph', () => {
  it('adds nodes with unique readable ids and their default parameters', () => {
    const first = addNode(normalizeGraph(null), spec('prodigal.predict'), { x: 10, y: 20 });
    const second = addNode(first.graph, spec('prodigal.predict'), { x: 300, y: 20 });
    expect([first.id, second.id]).toEqual(['prodigal_predict_1', 'prodigal_predict_2']);
    expect(second.graph.nodes.prodigal_predict_1.params).toEqual({ mode: 'meta', translation_table: 11 });
    expect(second.graph.layout).toEqual({ prodigal_predict_1: { x: 10, y: 20 }, prodigal_predict_2: { x: 300, y: 20 } });
  });

  it('replaces the link of a single input and appends to a multiple one only once', () => {
    let graph = chainGraph();
    graph = connect(graph, specs, ref('dl', 'genome'), ref('genes', 'genome'));
    expect(graph.nodes.genes.inputs?.genome).toEqual(ref('dl', 'genome'));
    graph = connect(graph, specs, ref('genes', 'proteins'), ref('report', 'evidence'));
    graph = connect(graph, specs, ref('genes', 'proteins'), ref('report', 'evidence'));
    expect(graph.nodes.report.inputs?.evidence).toEqual([ref('hits', 'hits'), ref('genes', 'proteins')]);
  });

  it('refuses a link to itself, between different types, out of the report, or one that closes a loop', () => {
    const graph = chainGraph();
    const problem = (target: typeof graph, source: [string, string], to: [string, string]) =>
      connectionProblem(target, specs, 'any', ref(...source), ref(...to));
    expect(problem(graph, ['genes', 'genes'], ['genes', 'genome'])).toBe('self');
    expect(problem(graph, ['dl', 'genome'], ['hits', 'query'])).toBe('type');
    expect(problem(graph, ['report', 'report'], ['hits', 'query'])).toBe('report');
    expect(problem(graph, ['genes', 'proteins'], ['report', 'evidence'])).toBeNull(); // the report reads any type

    const chain = normalizeGraph({
      nodes: {
        a: { type: 'prodigal.predict' },
        b: { type: 'prodigal.predict', inputs: { genome: ref('a', 'genes') } },
        c: { type: 'prodigal.predict', inputs: { genome: ref('b', 'genes') } },
      },
    });
    expect(problem(chain, ['b', 'genes'], ['a', 'genome'])).toBe('loop');
    expect(problem(chain, ['c', 'genes'], ['a', 'genome'])).toBe('loop');
    expect(problem(chain, ['a', 'genes'], ['c', 'genome'])).toBeNull();
  });

  it('removing a node removes its links and its position', () => {
    const graph = { ...chainGraph(), layout: { genes: { x: 1, y: 2 }, dl: { x: 0, y: 0 } } };
    const next = removeNode(graph, 'genes');
    expect(Object.keys(next.nodes)).toEqual(['dl', 'db', 'hits', 'report']);
    expect(next.nodes.db.inputs).toEqual({});
    expect(next.nodes.hits.inputs).toEqual({ db: ref('db', 'db') });
    expect(next.layout).toEqual({ dl: { x: 0, y: 0 } });
  });

  it('disconnecting drops one link and an input left empty', () => {
    const graph = chainGraph();
    const next = disconnect(graph, ref('genes', 'proteins'), ref('hits', 'query'));
    expect(next.nodes.hits.inputs).toEqual({ db: ref('db', 'db') });
    const report = disconnect(next, ref('hits', 'hits'), ref('report', 'evidence'));
    expect(report.nodes.report.inputs).toEqual({});
  });

  it('edge ids carry both ends', () => {
    const id = edgeId(ref('genes', 'proteins'), ref('hits', 'query'));
    expect(parseEdgeId(id)).toEqual({ source: ref('genes', 'proteins'), target: ref('hits', 'query') });
    expect(parseEdgeId('nonsense')).toBeNull();
  });

  it('accepts a partial or broken draft', () => {
    expect(normalizeGraph(undefined)).toEqual({ schema: 1, nodes: {}, layout: {} });
    expect(normalizeGraph({ nodes: [], layout: 'x' })).toEqual({ schema: 1, nodes: {}, layout: {} });
  });
});

describe('layout', () => {
  it('puts each step one column to the right of what it reads', () => {
    const layout = autoLayout(chainGraph());
    expect(layout.dl.x).toBeLessThan(layout.genes.x);
    expect(layout.genes.x).toBeLessThan(layout.db.x);
    expect(layout.db.x).toBeLessThan(layout.hits.x);
    expect(layout.hits.x).toBeLessThan(layout.report.x);
  });

  it('stacks the nodes of one column', () => {
    const graph = normalizeGraph({
      nodes: {
        dl: { type: 'ncbi.genome' },
        a: { type: 'prodigal.predict', inputs: { genome: ref('dl', 'genome') } },
        b: { type: 'prodigal.predict', inputs: { genome: ref('dl', 'genome') } },
      },
    });
    const layout = autoLayout(graph);
    expect(layout.a.x).toBe(layout.b.x);
    expect(layout.a.y).not.toBe(layout.b.y);
  });

  it('ends even when the graph has a loop', () => {
    const graph = normalizeGraph({
      nodes: {
        a: { type: 'prodigal.predict', inputs: { genome: ref('b', 'proteins') } },
        b: { type: 'prodigal.predict', inputs: { genome: ref('a', 'proteins') } },
      },
    });
    expect(Object.keys(autoLayout(graph)).sort()).toEqual(['a', 'b']);
  });

  it('keeps saved positions and fills in only the missing ones', () => {
    const graph = { ...chainGraph(), layout: { dl: { x: 500, y: 500 } } };
    const positions = positionsOf(graph);
    expect(positions.dl).toEqual({ x: 500, y: 500 });
    expect(Object.keys(positions).sort()).toEqual(['db', 'dl', 'genes', 'hits', 'report']);
  });

  it('places a new node next to, not on top of, an existing one', () => {
    const graph = { ...chainGraph(), layout: autoLayout(chainGraph()) };
    const taken = graph.layout!.dl;
    const spot = freeSpot(graph, taken);
    expect(Math.abs(spot.x - taken.x) > 240 || Math.abs(spot.y - taken.y) > 130).toBe(true);
    expect(freeSpot(normalizeGraph(null), { x: 12.4, y: 7.6 })).toEqual({ x: 12, y: 8 });
  });
});

describe('canvas elements', () => {
  it('gives each link the data type it carries', () => {
    const { nodes, edges } = toFlow(chainGraph(), specs);
    expect(nodes.map((node) => node.id)).toEqual(['dl', 'genes', 'db', 'hits', 'report']);
    const query = edges.find((edge) => edge.id === edgeId(ref('genes', 'proteins'), ref('hits', 'query')));
    expect(query).toMatchObject({ source: 'genes', sourceHandle: 'proteins', target: 'hits', targetHandle: 'query' });
    expect(query?.className).toContain('pl-type-fasta_aa');
  });

  it('skips nodes whose type the catalog does not know', () => {
    const graph = normalizeGraph({ nodes: { old: { type: 'gone.tool' }, dl: { type: 'ncbi.genome' } } });
    expect(toFlow(graph, specs).nodes.map((node) => node.id)).toEqual(['dl']);
  });
});
