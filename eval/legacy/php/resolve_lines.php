<?php

/**
 * Line recovery for legacy backend violations.
 *
 * The legacy PHP checkers report (file, import) but never a line number, while
 * the study's diff key is (file, line, target). This helper recovers the line
 * by re-running THE SAME PCRE PATTERN the reporting rule used, over the same
 * file, with PREG_OFFSET_CAPTURE, and returning the offset of the match whose
 * reported value the rule emitted. It is executed by PHP so the pattern is
 * interpreted by the same PCRE engine the legacy rule used — a Python
 * re-implementation of the regex would be a semantic change.
 *
 * Offset -> line uses substr_count(prefix, "\n") + 1, which is exactly the
 * conversion the legacy frontend auditor uses (getLineNumber()).
 *
 * Protocol: a JSON array of queries on stdin, a JSON array of line numbers
 * (or null) on stdout, positionally matched.
 *
 * Query: {"file": "<abs path>", "needle": "<reported value>",
 *         "mode": "capture"|"literal", "pattern": "<PCRE>"|null}
 *   capture — find the match of `pattern` whose whole match, any capture
 *             group, or the "\\"-join of two groups equals `needle`.
 *   literal — find the first occurrence of `needle` as a plain substring
 *             (used by rules whose mechanism is str_contains, not preg).
 */

declare(strict_types=1);

$input = stream_get_contents(STDIN);
$queries = json_decode($input, true);

if (! is_array($queries)) {
    fwrite(STDERR, "resolve_lines.php: invalid JSON on stdin\n");
    exit(2);
}

$cache = [];
$out = [];

foreach ($queries as $query) {
    $file = $query['file'] ?? null;
    $needle = $query['needle'] ?? null;
    $mode = $query['mode'] ?? 'capture';
    $pattern = $query['pattern'] ?? null;

    if (! is_string($file) || ! is_file($file)) {
        $out[] = null;
        continue;
    }

    if (! array_key_exists($file, $cache)) {
        $contents = file_get_contents($file);
        $cache[$file] = $contents === false ? null : $contents;
    }

    $contents = $cache[$file];

    if ($contents === null || ! is_string($needle)) {
        $out[] = null;
        continue;
    }

    $line = null;

    if ($mode === 'capture' && is_string($pattern)) {
        $ok = @preg_match_all($pattern, $contents, $matches, PREG_OFFSET_CAPTURE | PREG_SET_ORDER);

        if ($ok !== false) {
            foreach ($matches as $set) {
                $whole = $set[0][0];
                $groups = [];

                foreach ($set as $i => $group) {
                    if ($i === 0) {
                        continue;
                    }
                    $groups[] = $group[0];
                }

                $candidates = array_merge([$whole], $groups);

                if (count($groups) === 2) {
                    $candidates[] = $groups[0] . '\\' . $groups[1];
                }

                foreach ($candidates as $candidate) {
                    if ($candidate === $needle) {
                        $line = substr_count(substr($contents, 0, $set[0][1]), "\n") + 1;
                        break 2;
                    }
                }
            }
        }
    }

    if ($line === null) {
        // literal mode, and the fallback for capture mode: rules whose
        // reported value is a normalised form of the match (e.g. the aliased
        // import stripped by SharedBoundaryRulesTest) still contain the needle
        // verbatim in the source line.
        $offset = strpos($contents, $needle);

        if ($offset !== false) {
            $line = substr_count(substr($contents, 0, $offset), "\n") + 1;
        }
    }

    $out[] = $line;
}

echo json_encode($out), "\n";
