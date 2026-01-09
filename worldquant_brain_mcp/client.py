"""WorldQuant Brain API client."""

import time
import os
from typing import Optional, Dict, Any, List
from urllib.parse import urljoin

import requests

WQB_API = "https://api.worldquantbrain.com/"
RETRY_TIMES = 3


class BrainError(Exception):
    """Base exception for Brain API errors."""
    pass


class AuthenticationError(BrainError):
    """Authentication failed."""
    pass


class NetworkError(BrainError):
    """Network error occurred."""
    def __init__(self, err: Exception):
        self.inner = err
        super().__init__(f"Network error: {err}")


class SimulationError(BrainError):
    """Simulation API error."""
    def __init__(self, resp: requests.Response):
        self.resp = resp
        super().__init__(f"Simulation API error: {resp.status_code} - {resp.text}")


class BrainClient:
    """WorldQuant Brain API client."""
    
    def __init__(self, email: str, password: str, retry_times: int = RETRY_TIMES):
        self._email = email
        self._password = password
        self._session: Optional[requests.Session] = None
        self._retry_times = retry_times

    def connect(self):
        """Authenticate with the Brain API."""
        self._session = requests.Session()
        
        try:
            resp = self._session.post(
                urljoin(WQB_API, "authentication"),
                auth=requests.auth.HTTPBasicAuth(self._email, self._password),
            )
            if not resp.ok:
                raise AuthenticationError("API authentication failed")
        except requests.RequestException as e:
            raise NetworkError(e)

    def _send(self, req: requests.Request, retry_times: int = None) -> requests.Response:
        """Send a request with retry logic."""
        if self._session is None:
            self.connect()
        
        if retry_times is None:
            retry_times = self._retry_times
        
        try:
            prepared = self._session.prepare_request(req)
            resp = self._session.send(prepared)
            
            if resp.status_code == 401:
                self.connect()
                if retry_times > 0:
                    return self._send(req, retry_times - 1)
                raise AuthenticationError("Authentication failed after retry")
            
            return resp
        except requests.RequestException as e:
            if retry_times > 0:
                return self._send(req, retry_times - 1)
            raise NetworkError(e)

    def submit_simulation(
        self,
        expression: str,
        region: str = "USA",
        universe: str = "TOP3000",
        delay: int = 1,
        **settings
    ) -> str:
        """
        Submit a simulation to WorldQuant Brain.
        
        Args:
            expression: The alpha expression
            region: Market region (USA, CHN, EUR)
            universe: Universe (TOP3000, TOP500, etc.)
            delay: Trading delay in days
            **settings: Additional simulation settings
            
        Returns:
            Simulation ID
        """
        simulation_config = {
            "type": "REGULAR",
            "settings": {
                "instrumentType": "EQUITY",
                "region": region,
                "universe": universe,
                "delay": delay,
                "decay": settings.get("decay", 6),
                "neutralization": settings.get("neutralization", "SUBINDUSTRY"),
                "truncation": settings.get("truncation", 0.08),
                "pasteurization": settings.get("pasteurization", "ON"),
                "unitHandling": settings.get("unitHandling", "VERIFY"),
                "nanHandling": settings.get("nanHandling", "ON"),
                "language": settings.get("language", "FASTEXPR"),
                "visualization": settings.get("visualization", False),
            },
            "regular": expression,
        }
        
        req = requests.Request(
            method="POST",
            url=urljoin(WQB_API, "simulations"),
            json=simulation_config,
        )
        
        resp = self._send(req)
        if not resp.ok:
            raise SimulationError(resp)
        
        simulation_id = os.path.basename(resp.headers["Location"])
        return simulation_id

    def get_simulation_status(self, simulation_id: str) -> Dict[str, Any]:
        """Get the status of a simulation."""
        req = requests.Request(
            "GET",
            urljoin(WQB_API, f"simulations/{simulation_id}")
        )
        
        resp = self._send(req)
        if not resp.ok:
            raise SimulationError(resp)
        
        return resp.json()

    def wait_for_simulation(
        self,
        simulation_id: str,
        max_wait_time: int = 300,
        default_retry_after: float = 1.0
    ) -> Dict[str, Any]:
        """
        Wait for a simulation to complete.
        
        Args:
            simulation_id: The simulation ID
            max_wait_time: Maximum time to wait in seconds
            default_retry_after: Default wait time between checks
            
        Returns:
            Simulation result with alpha ID
        """
        start_time = time.time()
        fail_times = 0
        max_fail_times = 3
        
        while time.time() - start_time < max_wait_time:
            try:
                req = requests.Request(
                    "GET",
                    urljoin(WQB_API, f"simulations/{simulation_id}")
                )
                resp = self._send(req)
                
                if not resp.ok:
                    fail_times += 1
                    if fail_times > max_fail_times:
                        raise SimulationError(resp)
                    time.sleep(default_retry_after * (2 ** fail_times))
                    continue
                
                # Check if we need to wait
                if "Retry-After" in resp.headers:
                    retry_after = float(resp.headers["Retry-After"])
                    time.sleep(retry_after)
                    continue
                
                result = resp.json()
                
                # Check if simulation is complete
                if "alpha" in result:
                    return result
                
                # Wait before next check
                time.sleep(default_retry_after)
                
            except NetworkError:
                fail_times += 1
                if fail_times > max_fail_times:
                    raise
                time.sleep(default_retry_after * (2 ** fail_times))
        
        raise BrainError(f"Simulation {simulation_id} did not complete within {max_wait_time} seconds")

    def get_alpha(self, alpha_id: str) -> Dict[str, Any]:
        """Get details about an alpha."""
        req = requests.Request(
            "GET",
            urljoin(WQB_API, f"alphas/{alpha_id}")
        )
        
        resp = self._send(req)
        if not resp.ok:
            raise BrainError(f"Failed to get alpha {alpha_id}: {resp.text}")
        
        return resp.json()

    def list_alphas(self, limit: int = 10, offset: int = 0) -> List[Dict[str, Any]]:
        """List alphas."""
        req = requests.Request(
            "GET",
            urljoin(WQB_API, "alphas"),
            params={"limit": limit, "offset": offset}
        )
        
        resp = self._send(req)
        if not resp.ok:
            raise BrainError(f"Failed to list alphas: {resp.text}")
        
        result = resp.json()
        return result.get("results", [])
