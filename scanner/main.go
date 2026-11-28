// nullgate-scan: scanner de rede do Nullgate.
//
// Pipeline (modo padrao):
//   1. descoberta (arp-scan, fallback nmap -sn) -> hosts vivos + MAC + fabricante
//   2. varredura de portas concorrente (lista curada de portas comuns + IoT/camera/DVR)
//   3. deep-dive condicional via nmap NSE / whatweb, so nas portas que acharam algo
//
// Modo vuln (separado, nunca automatico):
//   -vulnhost <ip> -vulnports <csv>  -> roda nmap --script vuln so nesse host
//
// Escreve status continuamente em -statusfile (pra Python fazer polling) e o
// resultado final em -out (JSON por host + panorama).
package main

import (
	"bufio"
	"encoding/json"
	"flag"
	"fmt"
	"net"
	"os"
	"os/exec"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

// ---------- portas ----------

// lista curada: servicos comuns + IoT/camera/DVR (Intelbras, Hikvision, Dahua,
// TP-Link, Xiaomi, DVRs genericos chines, impressoras, NAS, UPnP/mDNS)
var portList = buildPortList()

func buildPortList() []int {
	raw := []int{
		21, 22, 23, 25, 53, 69, 80, 81, 82, 83, 84, 85, 88, 110, 111, 123, 135,
		137, 138, 139, 143, 161, 162, 179, 389, 443, 445, 465, 500, 502, 514,
		515, 548, 554, 587, 593, 631, 636, 689, 771, 873, 902, 990, 993, 995,
		1025, 1026, 1027, 1028, 1029, 1080, 1194, 1433, 1434, 1521, 1723, 1883,
		1900, 2000, 2020, 2049, 2082, 2083, 2086, 2087, 2095, 2096, 2181, 2222,
		2375, 2376, 27017, 3000, 3001, 3128, 3260, 3306, 3389, 3689, 3702, 4000,
		4040, 4443, 4567, 4848, 5000, 5001, 5002, 5003, 5060, 5061, 5353, 5357,
		5432, 5555, 5631, 5666, 5672, 5900, 5901, 5984, 5985, 5986, 6000, 6036,
		6379, 6443, 6660, 6667, 7000, 7001, 7002, 7547, 8000, 8001, 8008, 8080,
		8081, 8082, 8083, 8084, 8085, 8086, 8088, 8089, 8090, 8091, 8096, 8123,
		8181, 8200, 8333, 8443, 8500, 8530, 8531, 8765, 8800, 8834, 8880, 8883,
		8888, 8889, 8983, 9000, 9001, 9042, 9080, 9090, 9091, 9100, 9200, 9300,
		9418, 9443, 9999, 10000, 10001, 10243, 11211, 27015, 32400, 34567, 37777,
		47808, 49152,
	}
	seen := map[int]bool{}
	out := []int{}
	for _, p := range raw {
		if !seen[p] {
			seen[p] = true
			out = append(out, p)
		}
	}
	sort.Ints(out)
	return out
}

// portas que disparam deep-dive e pra que categoria
var (
	smbPorts = map[int]bool{139: true, 445: true}
	dnsPorts = map[int]bool{53: true}
	webPorts = map[int]bool{
		80: true, 81: true, 82: true, 83: true, 84: true, 85: true, 88: true,
		443: true, 8000: true, 8008: true, 8080: true, 8081: true, 8082: true,
		8083: true, 8084: true, 8085: true, 8088: true, 8443: true, 8880: true,
		8888: true, 8889: true, 9000: true,
	}
	iotHintPorts = map[int]bool{
		554: true, 8554: true, 37777: true, 34567: true, 7000: true, 7001: true,
		7002: true, 6036: true, 2020: true,
	}
)

// ---------- status / progresso ----------

type Status struct {
	Stage       string `json:"stage"` // discovery|ports|deepdive|done|error
	Progress    int    `json:"progress"`
	HostsFound  int    `json:"hosts_found"`
	CurrentHost string `json:"current_host,omitempty"`
	Message     string `json:"message,omitempty"`
	UpdatedAt   int64  `json:"updated_at"`
}

var statusPath string

func writeStatus(s Status) {
	s.UpdatedAt = time.Now().Unix()
	if statusPath == "" {
		return
	}
	b, _ := json.Marshal(s)
	tmp := statusPath + ".tmp"
	os.WriteFile(tmp, b, 0644)
	os.Rename(tmp, statusPath)
}

// ---------- modelo de dados ----------

type Host struct {
	IP        string            `json:"ip"`
	MAC       string            `json:"mac,omitempty"`
	Vendor    string            `json:"vendor,omitempty"`
	OpenPorts []int             `json:"open_ports"`
	Deepdive  map[string]string `json:"deepdive,omitempty"`
	Guess     string            `json:"guess,omitempty"`
}

type Result struct {
	Target    string    `json:"target"`
	StartedAt string    `json:"started_at"`
	FinishedAt string   `json:"finished_at"`
	Hosts     []*Host   `json:"hosts"`
	Panorama  string    `json:"panorama"`
}

// ---------- main ----------

func main() {
	target := flag.String("target", "", "faixa/IP alvo (ex: 10.6.1.0/24)")
	iface := flag.String("iface", "wlan1", "interface pro arp-scan")
	out := flag.String("out", "", "arquivo de saida JSON")
	status := flag.String("statusfile", "", "arquivo de status (polling)")
	workers := flag.Int("workers", 200, "concorrencia da varredura de portas")
	timeoutMs := flag.Int("timeout-ms", 700, "timeout por porta (ms)")
	vulnHost := flag.String("vulnhost", "", "modo vuln: so esse host")
	vulnPorts := flag.String("vulnports", "", "modo vuln: portas csv")
	flag.Parse()

	statusPath = *status

	if *vulnHost != "" {
		runVulnMode(*vulnHost, *vulnPorts, *out)
		return
	}

	if *target == "" {
		fmt.Fprintln(os.Stderr, "faltou -target")
		os.Exit(2)
	}

	res := &Result{Target: *target, StartedAt: time.Now().Format(time.RFC3339)}

	// estagio 1: descoberta
	writeStatus(Status{Stage: "discovery", Progress: 2, Message: "arp-scan em " + *iface})
	hosts, err := discoverHosts(*iface, *target)
	if err != nil {
		writeStatus(Status{Stage: "error", Message: err.Error()})
	}
	res.Hosts = hosts
	writeStatus(Status{Stage: "discovery", Progress: 10, HostsFound: len(hosts), Message: fmt.Sprintf("%d hosts encontrados", len(hosts))})

	if len(hosts) == 0 {
		res.FinishedAt = time.Now().Format(time.RFC3339)
		res.Panorama = "nenhum host respondeu na descoberta."
		saveResult(res, *out)
		writeStatus(Status{Stage: "done", Progress: 100, Message: "nenhum host encontrado"})
		return
	}

	// estagio 2: varredura de portas (concorrente entre hosts e portas)
	scanPorts(hosts, *workers, time.Duration(*timeoutMs)*time.Millisecond, func(doneHosts, totalHosts int, cur string) {
		pct := 10 + int(float64(doneHosts)/float64(totalHosts)*50) // 10..60
		writeStatus(Status{Stage: "ports", Progress: pct, HostsFound: len(hosts), CurrentHost: cur,
			Message: fmt.Sprintf("varrendo portas (%d/%d hosts)", doneHosts, totalHosts)})
	})

	// estagio 3: deep-dive condicional
	totalHosts := len(hosts)
	for i, h := range hosts {
		pct := 60 + int(float64(i)/float64(totalHosts)*35) // 60..95
		writeStatus(Status{Stage: "deepdive", Progress: pct, HostsFound: len(hosts), CurrentHost: h.IP,
			Message: "analisando " + h.IP})
		deepDive(h)
		h.Guess = guessDevice(h)
	}

	res.FinishedAt = time.Now().Format(time.RFC3339)
	res.Panorama = buildPanorama(res)
	saveResult(res, *out)
	writeStatus(Status{Stage: "done", Progress: 100, HostsFound: len(hosts), Message: "scan completo"})
}

func saveResult(res *Result, out string) {
	b, _ := json.MarshalIndent(res, "", "  ")
	if out == "" {
		fmt.Println(string(b))
		return
	}
	os.WriteFile(out, b, 0644)
}

// ---------- estagio 1: descoberta ----------

var arpLineRe = regexp.MustCompile(`^(\d+\.\d+\.\d+\.\d+)\s+([0-9a-fA-F:]{17})\s+(.*)$`)

func discoverHosts(iface, target string) ([]*Host, error) {
	if which("arp-scan") != "" {
		args := []string{"--localnet", "-I", iface, "-q"}
		cmd := exec.Command("arp-scan", args...)
		outB, _ := cmd.CombinedOutput()
		hosts := []*Host{}
		sc := bufio.NewScanner(strings.NewReader(string(outB)))
		for sc.Scan() {
			line := sc.Text()
			m := arpLineRe.FindStringSubmatch(line)
			if m == nil {
				continue
			}
			hosts = append(hosts, &Host{IP: m[1], MAC: strings.ToLower(m[2]), Vendor: strings.TrimSpace(m[3])})
		}
		if len(hosts) > 0 {
			return hosts, nil
		}
		// arp-scan rodou mas nao achou nada (ou deu erro silencioso) -> cai pro fallback
	}
	return discoverHostsNmap(target)
}

var nmapReportRe = regexp.MustCompile(`Nmap scan report for (?:(\S+) \()?([\d.]+)\)?`)
var nmapMacRe = regexp.MustCompile(`MAC Address: ([0-9A-Fa-f:]{17}) \((.*)\)`)

func discoverHostsNmap(target string) ([]*Host, error) {
	if target == "" {
		return nil, fmt.Errorf("sem -target pro fallback nmap")
	}
	cmd := exec.Command("nmap", "-sn", target)
	outB, err := cmd.CombinedOutput()
	if err != nil && len(outB) == 0 {
		return nil, err
	}
	hosts := []*Host{}
	var cur *Host
	sc := bufio.NewScanner(strings.NewReader(string(outB)))
	for sc.Scan() {
		line := sc.Text()
		if m := nmapReportRe.FindStringSubmatch(line); m != nil {
			cur = &Host{IP: m[2]}
			hosts = append(hosts, cur)
			continue
		}
		if m := nmapMacRe.FindStringSubmatch(line); m != nil && cur != nil {
			cur.MAC = strings.ToLower(m[1])
			cur.Vendor = m[2]
		}
	}
	return hosts, nil
}

// ---------- estagio 2: portas ----------

type portJob struct {
	host *Host
	port int
}

func scanPorts(hosts []*Host, workers int, timeout time.Duration, progress func(done, total int, cur string)) {
	jobs := make(chan portJob, 1024)
	var wg sync.WaitGroup
	var mu sync.Mutex

	for w := 0; w < workers; w++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for j := range jobs {
				addr := net.JoinHostPort(j.host.IP, strconv.Itoa(j.port))
				conn, err := net.DialTimeout("tcp", addr, timeout)
				if err == nil {
					conn.Close()
					mu.Lock()
					j.host.OpenPorts = append(j.host.OpenPorts, j.port)
					mu.Unlock()
				}
			}
		}()
	}

	go func() {
		for _, h := range hosts {
			for _, p := range portList {
				jobs <- portJob{host: h, port: p}
			}
		}
		close(jobs)
	}()

	wg.Wait()

	for i, h := range hosts {
		sort.Ints(h.OpenPorts)
		progress(i+1, len(hosts), h.IP)
	}
}

// ---------- estagio 3: deep-dive ----------

func deepDive(h *Host) {
	if len(h.OpenPorts) == 0 {
		return
	}
	h.Deepdive = map[string]string{}
	open := map[int]bool{}
	for _, p := range h.OpenPorts {
		open[p] = true
	}

	for p := range open {
		if smbPorts[p] {
			h.Deepdive["smb"] = nmapScript(h.IP, "139,445", "smb-os-discovery,smb-enum-shares,smb-enum-users")
			break
		}
	}
	for p := range open {
		if dnsPorts[p] {
			h.Deepdive["dns"] = nmapScript(h.IP, "53", "dns-recursion")
			break
		}
	}
	for _, port := range h.OpenPorts {
		if webPorts[port] {
			h.Deepdive["web"] = webProbe(h.IP, port)
			break
		}
	}
}

func nmapScript(ip, ports, scripts string) string {
	cmd := exec.Command("nmap", "-p", ports, "--host-timeout", "60s", "--script", scripts, ip)
	outB, _ := cmd.CombinedOutput()
	return trimOutput(string(outB), 2000)
}

func webProbe(ip string, port int) string {
	if which("whatweb") != "" {
		cmd := exec.Command("whatweb", "--no-errors", fmt.Sprintf("%s:%d", ip, port))
		outB, err := cmd.CombinedOutput()
		if err == nil && len(outB) > 0 {
			return trimOutput(string(outB), 1000)
		}
	}
	// fallback nativo nmap
	portStr := strconv.Itoa(port)
	return nmapScript(ip, portStr, "http-title,http-headers")
}

func trimOutput(s string, max int) string {
	s = strings.TrimSpace(s)
	if len(s) > max {
		return s[:max] + "...(cortado)"
	}
	return s
}

// ---------- modo vuln (manual, separado) ----------

func runVulnMode(ip, portsCSV, out string) {
	writeStatus(Status{Stage: "vuln", Progress: 5, CurrentHost: ip, Message: "rodando nmap --script vuln em " + ip})
	args := []string{"--host-timeout", "90s", "--script", "vuln"}
	if portsCSV != "" {
		args = append(args, "-p", portsCSV)
	}
	args = append(args, ip)
	cmd := exec.Command("nmap", args...)
	outB, _ := cmd.CombinedOutput()
	result := map[string]interface{}{
		"ip":     ip,
		"ports":  portsCSV,
		"output": string(outB),
		"ranAt":  time.Now().Format(time.RFC3339),
	}
	b, _ := json.MarshalIndent(result, "", "  ")
	if out != "" {
		os.WriteFile(out, b, 0644)
	} else {
		fmt.Println(string(b))
	}
	writeStatus(Status{Stage: "done", Progress: 100, CurrentHost: ip, Message: "vuln scan completo"})
}

// ---------- panorama e heuristica de device ----------

func guessDevice(h *Host) string {
	v := strings.ToLower(h.Vendor)
	open := map[int]bool{}
	for _, p := range h.OpenPorts {
		open[p] = true
	}
	switch {
	case strings.Contains(v, "hikvision") || strings.Contains(v, "dahua") || strings.Contains(v, "intelbras"):
		return "camera/DVR (" + h.Vendor + ")"
	case open[37777] || open[34567] || open[554] || open[8554]:
		return "provavel camera/DVR"
	case open[9100] || open[631] || open[515]:
		return "impressora"
	case open[445] && open[139]:
		return "windows/SMB"
	case open[5000] && open[5001]:
		return "NAS (Synology?)"
	case open[22] && !open[445]:
		return "linux/SSH"
	case strings.Contains(v, "tp-link"):
		return "roteador/AP TP-Link"
	case strings.Contains(v, "raspberry"):
		return "Raspberry Pi"
	case len(h.OpenPorts) == 0:
		return "desconhecido (sem porta aberta)"
	default:
		return "desconhecido"
	}
}

func buildPanorama(res *Result) string {
	var b strings.Builder
	fmt.Fprintf(&b, "scan de %s - %d hosts encontrados\n\n", res.Target, len(res.Hosts))
	for _, h := range res.Hosts {
		fmt.Fprintf(&b, "%-15s  %-17s  %-30s  portas:%v  -> %s\n",
			h.IP, h.MAC, h.Vendor, h.OpenPorts, h.Guess)
	}
	return b.String()
}

// ---------- util ----------

func which(bin string) string {
	p, err := exec.LookPath(bin)
	if err != nil {
		return ""
	}
	return p
}
