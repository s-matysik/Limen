<?php

/**
 * Minimal Laravel-shaped runtime for the unmodified legacy architecture tests.
 *
 * Provides exactly the global surface those classes touch:
 *   base_path(), config(), env(), database_path(), app_path(), storage_path()
 * plus a PSR-4 autoloader for App\, Tests\, Tests\Architecture\, PHPUnit\ and
 * LimenOracle\.
 *
 * LIMEN_BACKEND_ROOT selects the host backend repository to analyse (defaults
 * to the frozen workspace copy); the mutation harness points it at a temporary
 * clone.
 */

declare(strict_types=1);

define('LIMEN_BACKEND_ROOT', rtrim(
    getenv('LIMEN_BACKEND_ROOT') ?: dirname(__DIR__, 4) . '/workspace/litrev-backend',
    DIRECTORY_SEPARATOR
));

require __DIR__ . '/stubs/Recorder.php';

spl_autoload_register(static function (string $class): void {
    $stubs = __DIR__ . '/stubs';

    $prefixes = [
        'LimenOracle\\' => $stubs,
        'PHPUnit\\' => $stubs . '/PHPUnit',
        'Illuminate\\' => $stubs . '/Illuminate',
        'Tests\\Architecture\\' => LIMEN_BACKEND_ROOT . '/tests/Architecture',
        'Tests\\' => $stubs . '/Tests',
        'App\\' => LIMEN_BACKEND_ROOT . '/app',
    ];

    foreach ($prefixes as $prefix => $dir) {
        if (! str_starts_with($class, $prefix)) {
            continue;
        }

        $relative = substr($class, strlen($prefix));
        $path = $dir . '/' . str_replace('\\', '/', $relative) . '.php';

        if (is_file($path)) {
            require_once $path;

            return;
        }
    }
});

if (! function_exists('base_path')) {
    function base_path(string $path = ''): string
    {
        return $path === ''
            ? LIMEN_BACKEND_ROOT
            : LIMEN_BACKEND_ROOT . DIRECTORY_SEPARATOR . ltrim($path, '/');
    }
}

if (! function_exists('app_path')) {
    function app_path(string $path = ''): string
    {
        return base_path('app' . ($path === '' ? '' : '/' . ltrim($path, '/')));
    }
}

if (! function_exists('database_path')) {
    function database_path(string $path = ''): string
    {
        return base_path('database' . ($path === '' ? '' : '/' . ltrim($path, '/')));
    }
}

if (! function_exists('storage_path')) {
    function storage_path(string $path = ''): string
    {
        return base_path('storage' . ($path === '' ? '' : '/' . ltrim($path, '/')));
    }
}

if (! function_exists('env')) {
    function env(string $key, mixed $default = null): mixed
    {
        $value = getenv($key);

        if ($value === false) {
            return $default;
        }

        return match (strtolower($value)) {
            'true', '(true)' => true,
            'false', '(false)' => false,
            'null', '(null)' => null,
            'empty', '(empty)' => '',
            default => $value,
        };
    }
}

if (! function_exists('config')) {
    /**
     * Dot-notation reader over the host's config/*.php files, matching the
     * Laravel semantics the legacy tests rely on: config('architecture', [])
     * and config('database.connections', []).
     */
    function config(string $key, mixed $default = null): mixed
    {
        static $loaded = [];

        $segments = explode('.', $key);
        $file = array_shift($segments);

        if (! array_key_exists($file, $loaded)) {
            $path = base_path('config/' . $file . '.php');
            $loaded[$file] = is_file($path) ? require $path : null;
        }

        $value = $loaded[$file];

        foreach ($segments as $segment) {
            if (! is_array($value) || ! array_key_exists($segment, $value)) {
                return $default;
            }

            $value = $value[$segment];
        }

        return $value ?? $default;
    }
}
