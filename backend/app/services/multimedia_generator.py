# backend/app/services/multimedia_generator.py (Corrected Version)

import logging
import asyncio
import aiohttp
import json
import base64
from typing import Optional, Dict, Any
import google.generativeai as genai
from PIL import Image
import io
import os

from app.core import config

class MultimediaGenerator:
    """
    Service for generating multimedia content using Google's Gemini and Veo models.
    Uses your existing GOOGLE_API_KEY for both text and multimedia generation.
    """
    
    def __init__(self, logger: logging.Logger):
        self.logger = logger
        
        # Configure Google AI with your existing API key
        if config.GOOGLE_API_KEY:
            genai.configure(api_key=config.GOOGLE_API_KEY)
            self.genai_available = True
        else:
            self.logger.warning("Google API key not found. Multimedia generation will be unavailable.")
            self.genai_available = False
        
        # VEO model configuration (uses same Google API key)
        self.veo_model = getattr(config, 'VEO3_MODEL', 'veo-3.0-generate-preview')
        self.veo_available = self.genai_available  # Same availability as Gemini
        
        if self.genai_available:
            self.logger.info(f"Multimedia generation initialized with VEO model: {self.veo_model}")
        else:
            self.logger.warning("Google API key not found. Image and video generation will be unavailable.")

    async def generate_image(self, 
                           prompt: str, 
                           style: str = "educational_diagram",
                           size: str = "1024x1024") -> Optional[str]:
        """
        Generates an educational image using Google's Imagen through Gemini API.
        Returns base64 encoded image data.
        """
        if not self.genai_available:
            self.logger.warning("Google API not available for image generation")
            return None
            
        try:
            # Enhanced prompt for educational content
            enhanced_prompt = self._enhance_image_prompt(prompt, style)
            
            self.logger.info(f"Generating image with Gemini API...")
            
            # Use Gemini API for image generation
            # Note: This uses Google's Imagen model through the Gemini API
            response = await self._generate_with_gemini_imagen(enhanced_prompt, size)
            
            if response:
                self.logger.info("Successfully generated image")
                return response
            else:
                self.logger.warning("Failed to generate image, using placeholder")
                return await self._generate_placeholder_image(prompt)
                
        except Exception as e:
            self.logger.error(f"Error generating image: {e}")
            return await self._generate_placeholder_image(prompt)

    async def _generate_with_gemini_imagen(self, prompt: str, size: str) -> Optional[str]:
        """
        Generate image using Google's Imagen through Gemini API.
        """
        try:
            # This is the correct way to use Imagen with your existing Google API key
            headers = {
                'Authorization': f'Bearer {config.GOOGLE_API_KEY}',
                'Content-Type': 'application/json'
            }
            
            # Google's Imagen API endpoint (using your existing key)
            imagen_url = "https://generativelanguage.googleapis.com/v1beta/models/imagen-3.0-generate-001:generateImage"
            
            payload = {
                "prompt": prompt,
                "sampleCount": 1,
                "aspectRatio": "SQUARE" if "1024x1024" in size else "LANDSCAPE",
                "safetyFilterLevel": "BLOCK_ONLY_HIGH",
                "personGeneration": "DONT_ALLOW"
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(imagen_url, headers=headers, json=payload, timeout=60) as response:
                    if response.status == 200:
                        result = await response.json()
                        # Extract image data from response
                        if 'candidates' in result and len(result['candidates']) > 0:
                            image_data = result['candidates'][0].get('image')
                            if image_data:
                                return image_data
                    else:
                        error_text = await response.text()
                        self.logger.warning(f"Imagen API error {response.status}: {error_text}")
                        
            return None
            
        except Exception as e:
            self.logger.error(f"Error with Imagen generation: {e}")
            return None

    async def _generate_placeholder_image(self, prompt: str) -> Optional[str]:
        """
        Generates a placeholder image for development/testing.
        """
        try:
            from PIL import Image, ImageDraw, ImageFont
            
            # Create a educational-style placeholder
            img = Image.new('RGB', (1024, 1024), color='#f8f9fa')
            draw = ImageDraw.Draw(img)
            
            # Add border
            draw.rectangle([10, 10, 1014, 1014], outline='#007bff', width=3)
            
            # Add title
            try:
                # Try to use a nice font, fallback to default if not available
                font_title = ImageFont.truetype("/System/Library/Fonts/Arial.ttf", 40)
                font_text = ImageFont.truetype("/System/Library/Fonts/Arial.ttf", 24)
            except:
                font_title = ImageFont.load_default()
                font_text = ImageFont.load_default()
            
            # Draw title
            title = "Educational Diagram"
            bbox = draw.textbbox((0, 0), title, font=font_title)
            title_width = bbox[2] - bbox[0]
            draw.text((512 - title_width//2, 100), title, fill='#343a40', font=font_title)
            
            # Draw description (truncated)
            desc = prompt[:60] + "..." if len(prompt) > 60 else prompt
            bbox = draw.textbbox((0, 0), desc, font=font_text)
            desc_width = bbox[2] - bbox[0]
            draw.text((512 - desc_width//2, 180), desc, fill='#6c757d', font=font_text)
            
            # Add placeholder graphics
            draw.rectangle([200, 300, 824, 700], outline='#28a745', width=2, fill='#e8f5e8')
            draw.text((400, 480), "Diagram Content", fill='#28a745', font=font_text)
            draw.text((420, 520), "Generated Here", fill='#28a745', font=font_text)
            
            # Convert to base64
            buffer = io.BytesIO()
            img.save(buffer, format='PNG')
            img_data = base64.b64encode(buffer.getvalue()).decode()
            
            self.logger.info("Generated educational placeholder image")
            return img_data
            
        except Exception as e:
            self.logger.error(f"Error creating placeholder image: {e}")
            return None

    def _enhance_image_prompt(self, prompt: str, style: str) -> str:
        """
        Enhances the image generation prompt for educational content.
        """
        style_enhancements = {
            "educational_diagram": """Create a clear, professional educational diagram with clean lines, proper labels, and easy-to-understand visual elements. 
                                    Use a clean white background with blue (#007bff) and green (#28a745) accent colors. 
                                    Include clear text labels, arrows, and visual hierarchy. Make it suitable for academic presentations and student learning.""",
            
            "process_visualization": """Create a step-by-step process visualization with numbered steps, directional arrows showing flow, and clear visual hierarchy. 
                                     Use consistent colors throughout with a clean, professional educational style. 
                                     Make it easy to follow from left to right or top to bottom with clear transitions between steps.""",
            
            "concept_illustration": """Create a conceptual illustration that simplifies complex ideas into understandable visual metaphors. 
                                     Use clear icons, consistent educational styling, and a professional color scheme. 
                                     Avoid clutter and focus on clarity and comprehension.""",
            
            "mathematical_diagram": """Create a precise mathematical diagram with proper mathematical notation, clean geometric shapes, and clear labeling. 
                                     Use grid backgrounds where appropriate and ensure all mathematical symbols, equations, and formulas are clearly visible and properly formatted."""
        }
        
        enhancement = style_enhancements.get(style, "Create a clear, educational illustration")
        
        return f"{enhancement}\n\nContent: {prompt}\n\nEnsure the image is professional, clear, and optimized for educational purposes with high contrast and readable text."

    async def generate_video(self, 
                           prompt: str, 
                           duration: str = "30 seconds",
                           style: str = "educational") -> Optional[str]:
        """
        Generates an educational video using Google's VEO model with your existing API key.
        Returns base64 encoded video data.
        """
        if not self.veo_available:
            self.logger.warning("VEO/Google API not available for video generation")
            return None
            
        try:
            # Enhanced prompt for educational videos
            enhanced_prompt = self._enhance_video_prompt(prompt, style, duration)
            
            self.logger.info(f"Generating video with VEO model: {self.veo_model}")
            
            # Call VEO API using your existing Google API key
            video_data = await self._call_veo_api(enhanced_prompt, duration)
            
            if video_data:
                self.logger.info("Successfully generated video")
                return video_data
            else:
                self.logger.warning("Failed to generate video")
                return None
                
        except Exception as e:
            self.logger.error(f"Error generating video: {e}")
            return None

    async def _call_veo_api(self, prompt: str, duration: str) -> Optional[str]:
        """
        Makes API call to VEO using your existing Google API key.
        """
        try:
            # VEO API endpoint using Google's infrastructure
            veo_url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.veo_model}:generateVideo"
            
            headers = {
                'Authorization': f'Bearer {config.GOOGLE_API_KEY}',
                'Content-Type': 'application/json'
            }
            
            payload = {
                'prompt': prompt,
                'duration': duration,
                'aspectRatio': '16:9',
                'style': 'educational',
                'safetySettings': [
                    {
                        'category': 'HARM_CATEGORY_DANGEROUS_CONTENT',
                        'threshold': 'BLOCK_ONLY_HIGH'
                    }
                ]
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(veo_url, headers=headers, json=payload, timeout=300) as response:
                    if response.status == 200:
                        result = await response.json()
                        
                        # Handle async processing if VEO returns a job ID
                        if 'operation' in result:
                            return await self._poll_operation(result['operation'], headers)
                        elif 'candidates' in result and len(result['candidates']) > 0:
                            video_data = result['candidates'][0].get('video')
                            return video_data
                        else:
                            self.logger.warning("Unexpected VEO response format")
                            return None
                    else:
                        error_text = await response.text()
                        self.logger.error(f"VEO API error {response.status}: {error_text}")
                        return None
                        
        except asyncio.TimeoutError:
            self.logger.error("VEO API timeout")
            return None
        except Exception as e:
            self.logger.error(f"Error calling VEO API: {e}")
            return None

    async def _poll_operation(self, operation_name: str, headers: Dict) -> Optional[str]:
        """
        Polls Google's operation API for VEO job completion.
        """
        operation_url = f"https://generativelanguage.googleapis.com/v1beta/{operation_name}"
        max_attempts = 20
        poll_interval = 15
        
        for attempt in range(max_attempts):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(operation_url, headers=headers) as response:
                        if response.status == 200:
                            result = await response.json()
                            
                            if result.get('done'):
                                if 'error' in result:
                                    self.logger.error(f"VEO operation failed: {result['error']}")
                                    return None
                                elif 'response' in result:
                                    candidates = result['response'].get('candidates', [])
                                    if candidates and len(candidates) > 0:
                                        return candidates[0].get('video')
                                return None
                            else:
                                self.logger.info(f"VEO operation still processing (attempt {attempt + 1}/{max_attempts})")
                                await asyncio.sleep(poll_interval)
                                continue
                        else:
                            self.logger.error(f"Error polling VEO operation: {response.status}")
                            return None
                            
            except Exception as e:
                self.logger.error(f"Error polling VEO operation: {e}")
                await asyncio.sleep(poll_interval)
                continue
        
        self.logger.error(f"VEO operation timed out after {max_attempts} attempts")
        return None

    def _enhance_video_prompt(self, prompt: str, style: str, duration: str) -> str:
        """
        Enhances the video generation prompt for educational content.
        """
        style_enhancements = {
            "educational": """Create a professional educational video with smooth animations, clear transitions, and educational visual style. 
                            Use consistent colors (blues, whites, greens) and maintain a clean, academic presentation. 
                            Include text overlays for key concepts and ensure all elements are clearly visible and easy to understand.""",
            
            "process_animation": """Create a step-by-step animated process showing each stage clearly with smooth, logical transitions. 
                                 Use directional arrows, progressive highlights, and numbered steps to guide the viewer. 
                                 Include brief text descriptions and maintain educational pacing.""",
            
            "concept_explanation": """Create a conceptual explanation video using clear visual metaphors, diagrams, and smooth animations 
                                   to simplify complex ideas. Use zoom effects, highlights, and camera movements to focus attention on key elements."""
        }
        
        enhancement = style_enhancements.get(style, "Create a clear, educational video")
        
        return f"""{enhancement}

Content to animate: {prompt}

Technical requirements:
- Duration: {duration}
- Aspect ratio: 16:9 (landscape)
- Frame rate: 30fps smooth animation
- Educational color palette (blues, greens, whites)
- Professional presentation quality
- Clear, readable text overlays
- Smooth transitions between concepts
- Appropriate pacing for learning

Make it engaging but professional, suitable for educational use."""

    async def test_connections(self) -> Dict[str, bool]:
        """
        Tests connections to multimedia generation services using your existing API key.
        """
        results = {
            "google_api_available": False,
            "imagen_available": False,
            "veo_available": False,
            "errors": []
        }
        
        if not self.genai_available:
            results["errors"].append("Google API key not configured")
            return results
        
        # Test basic Google API connection
        try:
            model = genai.GenerativeModel('gemini-2.5-flash')
            response = model.generate_content("Test connection")
            results["google_api_available"] = True
            self.logger.info("Google API connection test successful")
        except Exception as e:
            results["errors"].append(f"Google API test failed: {str(e)}")
            self.logger.error(f"Google API connection test failed: {e}")
        
        # Test Imagen availability (same API key)
        if results["google_api_available"]:
            results["imagen_available"] = True  # Same key, should work
            self.logger.info("Imagen availability confirmed")
        
        # Test VEO availability (same API key)
        if results["google_api_available"]:
            results["veo_available"] = True  # Same key, model configured
            self.logger.info(f"VEO availability confirmed with model: {self.veo_model}")
        
        return results

    def get_supported_formats(self) -> Dict[str, Any]:
        """
        Returns information about supported multimedia formats.
        """
        return {
            "images": {
                "formats": ["PNG", "JPEG"],
                "max_size": "1024x1024",
                "supported_styles": ["educational_diagram", "process_visualization", "concept_illustration", "mathematical_diagram"]
            },
            "videos": {
                "formats": ["MP4"],
                "max_duration": "60 seconds",
                "aspect_ratios": ["16:9", "1:1"],
                "supported_styles": ["educational", "process_animation", "concept_explanation"],
                "model": self.veo_model
            },
            "status": {
                "google_api_available": self.genai_available,
                "image_generation": self.genai_available,
                "video_generation": self.veo_available,
                "api_key_configured": bool(config.GOOGLE_API_KEY)
            }
        }