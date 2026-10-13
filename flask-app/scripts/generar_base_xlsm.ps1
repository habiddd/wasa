# Genera flask-app/app/plantillas/base-activos.xlsm: libro vacio con macros,
# tres hojas (Inicio, Activos, Listas) y el VBA de ThisWorkbook.cls.txt e
# ImportarActivos.bas.txt. El servidor (app/importar_activos.py) rellena las
# hojas con openpyxl en cada descarga; este script solo se vuelve a ejecutar
# cuando cambia el VBA. Requiere Excel de escritorio.
# Activa temporalmente "Confiar en el acceso al modelo de objetos de proyectos
# de VBA" (HKCU ...\Excel\Security\AccessVBOM) y restaura el valor previo al final.

$ErrorActionPreference = 'Stop'

function Poner($componente, $codigo) {
    $cm = $componente.CodeModule
    if ($cm.CountOfLines -gt 0) { $cm.DeleteLines(1, $cm.CountOfLines) }   # el VBE puede haber puesto ya un Option Explicit
    $cm.AddFromString($codigo)
}

$dir = (Resolve-Path (Join-Path $PSScriptRoot '..\app\plantillas')).Path
$salida = Join-Path $dir 'base-activos.xlsm'
$codigoLibro  = Get-Content (Join-Path $dir 'ThisWorkbook.cls.txt') -Raw -Encoding UTF8
$codigoModulo = Get-Content (Join-Path $dir 'ImportarActivos.bas.txt') -Raw -Encoding UTF8

$clave = 'HKCU:\Software\Microsoft\Office\16.0\Excel\Security'
$existiaClave = Test-Path $clave
$previo = $null
if ($existiaClave) {
    $prop = Get-ItemProperty -Path $clave -Name AccessVBOM -ErrorAction SilentlyContinue
    if ($prop) { $previo = $prop.AccessVBOM }
}
else { New-Item -Path $clave -Force | Out-Null }
Set-ItemProperty -Path $clave -Name AccessVBOM -Value 1 -Type DWord

$excel = $null
try {
    $excel = New-Object -ComObject Excel.Application
    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    $wb = $excel.Workbooks.Add()
    while ($wb.Worksheets.Count -lt 3) { [void]$wb.Worksheets.Add([Type]::Missing, $wb.Worksheets.Item($wb.Worksheets.Count)) }
    while ($wb.Worksheets.Count -gt 3) { $wb.Worksheets.Item($wb.Worksheets.Count).Delete() }
    $nombres = 'Inicio', 'Activos', 'Listas'
    for ($i = 0; $i -lt 3; $i++) { $wb.Worksheets.Item($i + 1).Name = $nombres[$i] }

    # modulo del libro: se busca por tipo (100 = documento) y por no ser una hoja
    $libro = $null
    foreach ($c in $wb.VBProject.VBComponents) {
        if ($c.Type -eq 100 -and $c.Name -eq $wb.CodeName) { $libro = $c }
    }
    if (-not $libro) { $libro = $wb.VBProject.VBComponents.Item('ThisWorkbook') }

    Poner $libro $codigoLibro
    $modulo = $wb.VBProject.VBComponents.Add(1)   # 1 = modulo estandar
    $modulo.Name = 'ImportarActivos'
    Poner $modulo $codigoModulo

    if (Test-Path $salida) { Remove-Item $salida -Force }
    $wb.SaveAs($salida, 52)   # 52 = xlOpenXMLWorkbookMacroEnabled
    $wb.Close($false)
    Write-Output "Generado: $salida"
}
finally {
    if ($excel) {
        # Excel reescribe su configuracion de seguridad al cerrarse: hay que
        # esperar a que el proceso termine ANTES de restaurar AccessVBOM, o lo
        # deja en 1.
        $hwnd = $excel.Hwnd
        $proceso = Get-Process EXCEL -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -eq $hwnd }
        $excel.Quit()
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($excel)
        [GC]::Collect()
        [GC]::WaitForPendingFinalizers()
        if ($proceso) { [void]$proceso.WaitForExit(20000) } else { Start-Sleep -Seconds 5 }
    }
    if ($null -ne $previo) { Set-ItemProperty -Path $clave -Name AccessVBOM -Value $previo -Type DWord }
    else {
        Remove-ItemProperty -Path $clave -Name AccessVBOM -ErrorAction SilentlyContinue
        if (-not $existiaClave) { Remove-Item -Path $clave -ErrorAction SilentlyContinue }
    }
}
