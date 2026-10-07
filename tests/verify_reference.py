"""Optional regression against existing analysis tables; never ships experimental data."""
import argparse
import json
from pathlib import Path

import pandas as pd

from linux.analysis_worker import analyze_file, load_config


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True)
    parser.add_argument('--expected-dir', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    result = analyze_file(args.input, args.output, load_config(root/'linux'/'analysis.yaml'), 'maxlab')
    expected = Path(args.expected_dir)
    for name, reference in [('electrodes', 'electrode_metrics_and_high_outdegree'),
                             ('edges', 'directed_connections'), ('burst_table', 'bursts'),
                             ('activation', 'first_activation_metrics')]:
        actual = pd.read_csv(Path(args.output)/(name+'.csv'))
        original = pd.read_csv(expected/(reference+'.csv'))
        # Historical tables used a CFG coordinate override. This service uses
        # the recording's H5 mapping; compare all network metrics separately.
        pd.testing.assert_frame_equal(actual.drop(columns=['x_um', 'y_um'], errors='ignore'),
            original.drop(columns=['x_um', 'y_um'], errors='ignore'),
            check_dtype=False, rtol=1e-8, atol=1e-8)
    report = dict(tables_equal=True, candidates=[r['electrode'] for r in result['candidates']],
                  threshold=result['threshold'], burst_count=result['burst_count'],
                  edge_count=result['edge_count'], candidate_count=result['candidate_count'])
    (Path(args.output)/'reference_check.json').write_text(json.dumps(report, indent=2), 'utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
