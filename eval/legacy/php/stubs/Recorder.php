<?php

/**
 * Collects assertion events emitted by the unmodified legacy architecture
 * tests. Contains no rule logic: it only stores what the legacy classes
 * already computed, plus the decoded JSON payload the legacy classes
 * themselves append to their assertion messages.
 */

namespace LimenOracle;

final class Recorder
{
    /** @var array<int, array<string, mixed>> */
    public static array $events = [];

    public static ?string $currentClass = null;
    public static ?string $currentMethod = null;
    public static ?string $currentDataset = null;

    public static function context(?string $class, ?string $method, ?string $dataset): void
    {
        self::$currentClass = $class;
        self::$currentMethod = $method;
        self::$currentDataset = $dataset;
    }

    public static function record(
        string $assertion,
        mixed $expected,
        mixed $actual,
        string $message,
        bool $passed,
        array $extra = []
    ): void {
        self::$events[] = [
            'class' => self::$currentClass,
            'method' => self::$currentMethod,
            'dataset' => self::$currentDataset,
            'assertion' => $assertion,
            'passed' => $passed,
            'message' => $message,
            'expected' => $expected,
            'actual' => $actual,
            'payload' => self::decodePayload($message),
            'extra' => $extra,
        ];
    }

    /**
     * The legacy classes encode their violation list into the assertion
     * message as "<human prefix>: <json>". This recovers that JSON exactly as
     * the legacy class serialised it (json_encode with JSON_UNESCAPED_SLASHES).
     */
    private static function decodePayload(string $message): mixed
    {
        $start = null;

        foreach (['[', '{'] as $open) {
            $candidate = strpos($message, ': ' . $open);

            if ($candidate !== false && ($start === null || $candidate < $start)) {
                $start = $candidate;
            }
        }

        if ($start === null) {
            return null;
        }

        $json = substr($message, $start + 2);
        $decoded = json_decode($json, true);

        return json_last_error() === JSON_ERROR_NONE ? $decoded : null;
    }
}
