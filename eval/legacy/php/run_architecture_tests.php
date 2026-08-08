<?php

/**
 * Oracle driver for the legacy backend architecture checkers.
 *
 * Loads every tests/Architecture/*Test.php class UNMODIFIED, discovers its
 * test_* methods by reflection (no hand-written method list), resolves
 * #[DataProvider] datasets, runs them, and dumps every assertion event as
 * JSON on stdout.
 *
 * This file contains no rule logic. The only non-trivial thing it does beyond
 * invocation is LINE RECOVERY: the legacy PHP checkers report (file, import)
 * but never a line number, and the diff key for the study is
 * (file, line, target). Lines are recovered by re-running THE SAME PCRE
 * pattern the reporting rule used, over the same file, with
 * PREG_OFFSET_CAPTURE, and taking the offset of the match whose capture
 * equals the reported value. Offset -> line uses substr_count(prefix, "\n")+1,
 * the same conversion the legacy frontend auditor uses (getLineNumber).
 *
 * Usage:
 *   php run_architecture_tests.php [--backend=/abs/path/to/litrev-backend]
 */

declare(strict_types=1);

require __DIR__ . '/bootstrap.php';

use LimenOracle\Recorder;

// ---------------------------------------------------------------------------
// Line recovery: the pattern each legacy rule used to produce its match.
// Keyed by "<Class>::<method>". 'pattern' is copied verbatim from the legacy
// source; 'mode' says how the reported value relates to the match.
// ---------------------------------------------------------------------------

final class LineRecovery
{
    /**
     * Locate the line of a reported violation inside $absFile.
     *
     * @param string      $absFile absolute path of the analysed file
     * @param string|null $pattern legacy PCRE pattern, capture group 1 = import
     * @param string|null $needle  the value the legacy rule reported
     * @param string      $mode    'capture' | 'literal'
     */
    public static function line(string $absFile, ?string $pattern, ?string $needle, string $mode = 'capture'): ?int
    {
        if (! is_file($absFile)) {
            return null;
        }

        $contents = file_get_contents($absFile);

        if ($contents === false) {
            return null;
        }

        if ($mode === 'literal' && $needle !== null) {
            $offset = strpos($contents, $needle);

            return $offset === false ? null : self::offsetToLine($contents, $offset);
        }

        if ($pattern === null || $needle === null) {
            return null;
        }

        if (preg_match_all($pattern, $contents, $matches, PREG_OFFSET_CAPTURE | PREG_SET_ORDER) === false) {
            return null;
        }

        foreach ($matches as $set) {
            // Try every capture group: different legacy rules report group 1
            // (full import), group 1 of a two-group pattern (module name), or
            // a concatenation of groups.
            $whole = $set[0][0];
            $groups = [];

            foreach ($set as $i => $g) {
                if ($i === 0) {
                    continue;
                }
                $groups[] = $g[0];
            }

            $candidates = array_merge([$whole], $groups);

            if (count($groups) === 2) {
                $candidates[] = $groups[0] . '\\' . $groups[1];
            }

            foreach ($candidates as $candidate) {
                if ($candidate === $needle) {
                    return self::offsetToLine($contents, $set[0][1]);
                }
            }
        }

        // Fall back to a literal search for the needle (covers rules whose
        // reported value is a normalised form of the match, e.g. the aliased
        // import stripped by SharedBoundaryRulesTest, or a reconstructed FQCN).
        $offset = strpos($contents, $needle);

        return $offset === false ? null : self::offsetToLine($contents, $offset);
    }

    public static function offsetToLine(string $text, int $offset): int
    {
        return substr_count(substr($text, 0, $offset), "\n") + 1;
    }
}

// ---------------------------------------------------------------------------
// Discovery + execution
// ---------------------------------------------------------------------------

$options = getopt('', ['backend::']);
$architectureDir = LIMEN_BACKEND_ROOT . '/tests/Architecture';

$files = glob($architectureDir . '/*.php') ?: [];
sort($files);

$classes = [];

foreach ($files as $file) {
    $class = 'Tests\\Architecture\\' . pathinfo($file, PATHINFO_FILENAME);

    if (! class_exists($class)) {
        continue;
    }

    $reflection = new ReflectionClass($class);

    if ($reflection->isAbstract()) {
        continue;
    }

    $classes[$class] = $reflection;
}

$executions = [];
$errors = [];

foreach ($classes as $className => $reflection) {
    $methods = [];

    foreach ($reflection->getMethods(ReflectionMethod::IS_PUBLIC) as $method) {
        if ($method->getDeclaringClass()->getName() !== $className) {
            continue;
        }

        if (! str_starts_with($method->getName(), 'test_')) {
            continue;
        }

        $methods[] = $method;
    }

    usort($methods, static fn ($a, $b) => strcmp($a->getName(), $b->getName()));

    foreach ($methods as $method) {
        $noDataset = '__no_dataset__';
        $datasets = [$noDataset => []];

        foreach ($method->getAttributes() as $attribute) {
            if ($attribute->getName() !== 'PHPUnit\\Framework\\Attributes\\DataProvider') {
                continue;
            }

            $providerName = $attribute->getArguments()[0];
            $datasets = $reflection->getMethod($providerName)->invoke(null);
        }

        foreach ($datasets as $datasetName => $arguments) {
            $before = count(Recorder::$events);
            $key = $datasetName === $noDataset ? null : (string) $datasetName;

            Recorder::context($className, $method->getName(), $key);

            try {
                /** @var object $instance */
                $instance = $reflection->newInstance();

                $reflection->getMethod('setUp')->invoke($instance);

                $method->invokeArgs($instance, is_array($arguments) ? $arguments : [$arguments]);

                $status = 'ran';
                $error = null;
            } catch (Throwable $throwable) {
                $status = 'error';
                $error = $throwable::class . ': ' . $throwable->getMessage();
                $errors[] = [
                    'class' => $className,
                    'method' => $method->getName(),
                    'dataset' => $key,
                    'error' => $error,
                ];
            }

            $after = count(Recorder::$events);
            $slice = array_slice(Recorder::$events, $before, $after - $before);

            $executions[] = [
                'class' => $className,
                'method' => $method->getName(),
                'dataset' => $key,
                'status' => $status,
                'error' => $error,
                'assertions' => count($slice),
                'failed_assertions' => count(array_filter($slice, static fn ($e) => ! $e['passed'])),
            ];

            Recorder::context(null, null, null);
        }
    }
}

// ---------------------------------------------------------------------------
// Per-rule line-recovery patterns, copied verbatim from the legacy sources.
// ---------------------------------------------------------------------------

$architecture = config('architecture', []);

$patterns = [
    // AdminBoundaryRulesTest: str_contains on a literal 'use App\Modules\X\'
    'Tests\Architecture\AdminBoundaryRulesTest::test_module_does_not_import_admin_modules_directly'
        => ['mode' => 'literal'],
    // ApplicationBoundaryRulesTest.php:34
    'Tests\Architecture\ApplicationBoundaryRulesTest::test_modules_do_not_import_foreign_application_internals'
        => ['mode' => 'capture', 'pattern' => '/use\s+App\\\\Modules\\\\([^\\\\]+)\\\\Application\\\\([^;]+);/m'],
    // HttpBoundaryRulesTest.php:20
    'Tests\Architecture\HttpBoundaryRulesTest::test_module_does_not_import_http_layer_of_other_modules'
        => ['mode' => 'capture', 'pattern' => '/use\s+App\\\\Modules\\\\([^\\\\]+)\\\\Http\\\\/m'],
    // LayerBoundaryRulesTest.php:22
    'Tests\Architecture\LayerBoundaryRulesTest::test_application_layer_does_not_import_module_infrastructure'
        => ['mode' => 'capture', 'pattern' => '/use\s+App\\\\Modules\\\\([^\\\\]+)\\\\Infrastructure\\\\([^;]+);/m'],
    // LayerBoundaryRulesTest.php:50
    'Tests\Architecture\LayerBoundaryRulesTest::test_models_do_not_import_module_application_classes'
        => ['mode' => 'capture', 'pattern' => '/use\s+(App\\\\Modules\\\\[^;]+\\\\Application\\\\[^;]+);/m'],
    // ModelOwnershipRulesTest.php:71
    'Tests\Architecture\ModelOwnershipRulesTest::test_model_imports_are_limited_to_owner_or_declared_consumers'
        => ['mode' => 'capture', 'pattern' => '/use\s+(App\\\\Models\\\\[^;]+);/m'],
    // SharedBoundaryRulesTest.php:54
    'Tests\Architecture\SharedBoundaryRulesTest::test_shared_does_not_import_forbidden_host_classes'
        => ['mode' => 'capture', 'pattern' => '/use\s+(App\\\\[^;]+);/m'],
    // SharedBoundaryRulesTest.php:97
    'Tests\Architecture\SharedBoundaryRulesTest::test_shared_cross_module_imports_use_contracts_events_domain_or_presentation'
        => ['mode' => 'capture', 'pattern' => '/use\s+LitRev\\\\BackendShared\\\\([^\\\\]+)\\\\([^;]+);/m'],
];

// LayerBoundaryRulesTest support/model allowlist drivers and
// ModuleContractRulesTest build their pattern from config at runtime; those are
// resolved per-violation below.

$out = [
    'schema' => 'legacy-oracle-raw/1',
    'backend_root' => LIMEN_BACKEND_ROOT,
    'php_version' => PHP_VERSION,
    'generated_at' => gmdate('c'),
    'classes' => array_keys($classes),
    'executions' => $executions,
    'errors' => $errors,
    'config_patterns' => [
        'support_module_import_rules' => array_map(
            static fn (array $r) => ['path' => $r['path'], 'pattern' => $r['pattern']],
            $architecture['support_module_import_rules'] ?? []
        ),
        'model_module_import_rules' => array_map(
            static fn (array $r) => ['path' => $r['path'], 'pattern' => $r['pattern']],
            $architecture['model_module_import_rules'] ?? []
        ),
        'module_application_rules' => array_map(
            static fn (array $r) => [
                'path' => $r['path'],
                'pattern' => '/use\s+(' . preg_quote($r['import_prefix'], '/') . '\\\\[^;]+);/m',
                'message' => $r['message'],
            ],
            $architecture['module_application_rules'] ?? []
        ),
        'forbidden_model_import_rules' => array_map(
            static fn (array $r) => [
                'path' => $r['path'],
                'literal' => 'use ' . $r['model'] . ';',
                'message' => $r['message'],
            ],
            $architecture['forbidden_model_import_rules'] ?? []
        ),
    ],
    'shared_src' => base_path('shared') . '/src',
    'line_patterns' => $patterns,
    'events' => Recorder::$events,
];

echo json_encode($out, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT), "\n";
