<?php

/**
 * Minimal stand-in for Illuminate\Support\Str.
 *
 * Reached from exactly one place in the analysed host repository:
 * config/database.php:151, `Str::slug((string) env('APP_NAME','laravel'))`,
 * used to build the redis key prefix. The only architecture rule that reads
 * this file (DeploymentBoundaryRulesTest::
 * test_database_config_does_not_define_module_scoped_connections) inspects
 * array_keys(config('database.connections')) — the 'redis' section and the
 * value produced here never enter any assertion. The implementation is
 * therefore only required to exist and return a string.
 */

namespace Illuminate\Support;

final class Str
{
    public static function slug(string $title, string $separator = '-'): string
    {
        $value = strtolower($title);
        $value = preg_replace('/[^a-z0-9]+/', $separator, $value) ?? '';

        return trim($value, $separator);
    }
}
