<?php

namespace PHPUnit\Framework\Attributes;

use Attribute;

#[Attribute(Attribute::TARGET_METHOD)]
final class DataProvider
{
    public function __construct(public readonly string $methodName)
    {
    }
}
