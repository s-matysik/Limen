<?php

/**
 * Stand-in for the application's Tests\TestCase.
 *
 * The real class boots a full Laravel application only so that config() and
 * base_path() resolve and so that the test database is forced to sqlite. None
 * of the tests/Architecture/* classes touch the container, the database, the
 * HTTP kernel or any facade — they read files from disk and read two config
 * keys ('architecture', 'database.connections'). This stub therefore provides
 * setUp()/tearDown() only; the two config keys are served by the config()
 * helper in bootstrap.php, which loads the host's real config/*.php files.
 */

namespace Tests;

use PHPUnit\Framework\TestCase as BaseTestCase;

abstract class TestCase extends BaseTestCase
{
    protected function setUp(): void
    {
        parent::setUp();
    }
}
