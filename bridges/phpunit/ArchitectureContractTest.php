<?php

declare(strict_types=1);

namespace Tests\Architecture;

use PHPUnit\Framework\TestCase;

/**
 * Runs `limen check` as a PHPUnit test, so the architecture contract fails
 * the same suite the unit tests do. Copy this file into tests/Architecture/
 * and adjust LIMEN_BIN / SPEC if needed.
 *
 * Requires: pip install limen-arch (limen on PATH), architecture.yaml in the
 * repository root.
 */
final class ArchitectureContractTest extends TestCase
{
    private const LIMEN_BIN = 'limen';
    private const SPEC = 'architecture.yaml';

    public function testArchitectureContractHolds(): void
    {
        $root = \dirname(__DIR__, 2); // tests/Architecture -> repo root
        $cmd = [
            self::LIMEN_BIN, 'check',
            '--spec', $root . '/' . self::SPEC,
            '--workspace', $root,
            '--format', 'json',
        ];

        $process = \proc_open(
            $cmd,
            [1 => ['pipe', 'w'], 2 => ['pipe', 'w']],
            $pipes,
            $root
        );
        $this->assertIsResource($process, 'could not start limen');

        $stdout = \stream_get_contents($pipes[1]);
        $stderr = \stream_get_contents($pipes[2]);
        \fclose($pipes[1]);
        \fclose($pipes[2]);
        $exit = \proc_close($process);

        if ($exit === 3 || $exit === 4) {
            $this->fail("limen infrastructure error (exit {$exit}):\n{$stderr}");
        }

        $report = \json_decode((string) $stdout, true);
        $this->assertIsArray($report, "limen produced no JSON report:\n{$stderr}");

        $errors = $report['summary']['errors'] ?? -1;
        $messages = [];
        foreach ($report['findings'] ?? [] as $f) {
            if (($f['severity'] ?? '') !== 'error') {
                continue;
            }
            $where = ($f['file'] ?? '?') . (isset($f['line']) ? ':' . $f['line'] : '');
            $messages[] = "[{$f['rule_id']}] {$where}\n"
                . '    ' . ($f['message'] ?? '')
                . (isset($f['remedy']) ? "\n    Remedy: {$f['remedy']}" : '');
        }

        $this->assertSame(
            0,
            $errors,
            "Architecture contract violated:\n" . \implode("\n", $messages)
        );
    }
}
