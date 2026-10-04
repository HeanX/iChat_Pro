param(
  [Parameter(Mandatory = $true)][string]$Executable,
  [Parameter(Mandatory = $true)][string]$ExpectedVersion
)
$ErrorActionPreference = 'Stop'
$metadata = (Get-Item -LiteralPath $Executable).VersionInfo
$expected = @{
  ProductName = 'iChat Pro'
  FileDescription = 'iChat Pro desktop client'
  CompanyName = 'iChat Pro Team'
  InternalName = 'iChat Pro'
  OriginalFilename = 'iChat Pro.exe'
  FileVersion = $ExpectedVersion
  ProductVersion = $ExpectedVersion
}
foreach ($field in $expected.Keys) {
  if ($metadata.$field -cne $expected[$field]) {
    throw "Unexpected ${field}: '$($metadata.$field)' (expected '$($expected[$field])')"
  }
}

[ordered]@{
    ProductName = $metadata.ProductName
    FileDescription = $metadata.FileDescription
    InternalName = $metadata.InternalName
    OriginalFilename = $metadata.OriginalFilename
    FileVersion = $metadata.FileVersion
    ProductVersion = $metadata.ProductVersion
} | ConvertTo-Json -Compress
